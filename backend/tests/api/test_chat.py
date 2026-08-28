"""Direct chat with one agent (oc8.chat.service). Each message is a real
AgentRun (source="chat"), so this exercises the full HTTP surface --
session create/list, message send/list -- against a real app instance."""

from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role="org_admin")
    return {"Authorization": f"Bearer {token}"}


async def _seed_agent(app_session: AppSessionFactory, tenant: uuid.UUID) -> uuid.UUID:
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Helpdesk", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Lennart")
        db.add(agent)
        await db.flush()
        return agent.id


async def test_create_session_requires_a_visible_agent(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/chat/sessions",
                json={"agentId": str(uuid.uuid4())},
                headers=_headers(tenant),
            )
            assert r.status_code == 404, r.text


async def test_create_session_then_list_it(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    agent_id = await _seed_agent(app_session, tenant)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            create_r = await c.post(
                "/api/v1/chat/sessions",
                json={"agentId": str(agent_id)},
                headers=_headers(tenant),
            )
            assert create_r.status_code == 201, create_r.text
            session_id = create_r.json()["id"]

            list_r = await c.get(
                f"/api/v1/chat/sessions?agentId={agent_id}", headers=_headers(tenant)
            )
            assert list_r.status_code == 200, list_r.text
            ids = [s["id"] for s in list_r.json()]
            assert session_id in ids


async def test_send_message_creates_a_chat_source_run(
    app_session: AppSessionFactory, redis_url: str
) -> None:
    # redis_url must be REQUESTED, not merely available: enqueue_run publishes
    # onto the run queue, which lazily binds to the testcontainer only when a
    # test in the current session has asked for it first.
    tenant = uuid.uuid4()
    agent_id = await _seed_agent(app_session, tenant)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            create_r = await c.post(
                "/api/v1/chat/sessions",
                json={"agentId": str(agent_id)},
                headers=_headers(tenant),
            )
            session_id = create_r.json()["id"]

            send_r = await c.post(
                f"/api/v1/chat/sessions/{session_id}/messages",
                json={"message": "Hallo!"},
                headers=_headers(tenant),
            )
            assert send_r.status_code == 201, send_r.text
            body = send_r.json()
            assert body["role"] == "user"
            assert body["content"] == "Hallo!"

    async with app_session(tenant) as db:
        run = (
            await db.execute(
                select(m.AgentRun).where(
                    m.AgentRun.agent_id == agent_id, m.AgentRun.source == "chat"
                )
            )
        ).scalar_one()
        assert run.context["chat_session_id"] == session_id
        assert run.context["task"] == "User: Hallo!"


async def test_rendered_components_reach_the_wire_as_camel_case(
    app_session: AppSessionFactory,
) -> None:
    """Regression test: `rendered_components` is stored as plain
    `{"component_key": ..., "props": ...}` dicts (control_tools.py's
    ControlOutcome, never itself a CamelModel). A DTO field typed as a bare
    `dict[str, object]` bypasses CamelModel's alias generator entirely, so
    the wire response silently kept the snake_case key -- the frontend's
    `c.componentKey` lookup then read `undefined` and rendered nothing,
    even though the data had round-tripped through the database correctly."""
    tenant = uuid.uuid4()
    agent_id = await _seed_agent(app_session, tenant)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            create_r = await c.post(
                "/api/v1/chat/sessions",
                json={"agentId": str(agent_id)},
                headers=_headers(tenant),
            )
            session_id = create_r.json()["id"]

    async with app_session(tenant) as db:
        db.add(
            m.ChatMessage(
                tenant_id=tenant,
                session_id=session_id,
                role="assistant",
                content="Hier ist die Tabelle.",
                rendered_components=[
                    {"component_key": "data_table", "props": {"title": "Tickets"}}
                ],
            )
        )

    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get(
                f"/api/v1/chat/sessions/{session_id}/messages", headers=_headers(tenant)
            )
            assert r.status_code == 200, r.text
            components = r.json()[0]["renderedComponents"]
            assert components == [{"componentKey": "data_table", "props": {"title": "Tickets"}}]


async def test_send_message_to_a_foreign_session_is_refused(
    app_session: AppSessionFactory,
) -> None:
    """A session belongs to the member who created it -- another operator's
    token must not be able to post into it, even inside the same tenant."""
    tenant = uuid.uuid4()
    agent_id = await _seed_agent(app_session, tenant)
    app = create_app()
    other_token = get_identity_provider().mint(
        tenant_id=tenant, subject="other-operator", role="org_admin"
    )
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            create_r = await c.post(
                "/api/v1/chat/sessions",
                json={"agentId": str(agent_id)},
                headers=_headers(tenant),
            )
            session_id = create_r.json()["id"]

            r = await c.post(
                f"/api/v1/chat/sessions/{session_id}/messages",
                json={"message": "Hallo!"},
                headers={"Authorization": f"Bearer {other_token}"},
            )
            assert r.status_code == 404, r.text


async def test_get_messages_returns_them_in_order(
    app_session: AppSessionFactory, redis_url: str
) -> None:
    tenant = uuid.uuid4()
    agent_id = await _seed_agent(app_session, tenant)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            create_r = await c.post(
                "/api/v1/chat/sessions",
                json={"agentId": str(agent_id)},
                headers=_headers(tenant),
            )
            session_id = create_r.json()["id"]
            await c.post(
                f"/api/v1/chat/sessions/{session_id}/messages",
                json={"message": "erstens"},
                headers=_headers(tenant),
            )
            await c.post(
                f"/api/v1/chat/sessions/{session_id}/messages",
                json={"message": "zweitens"},
                headers=_headers(tenant),
            )
            r = await c.get(
                f"/api/v1/chat/sessions/{session_id}/messages", headers=_headers(tenant)
            )
            assert r.status_code == 200, r.text
            contents = [m["content"] for m in r.json()]
            assert contents == ["erstens", "zweitens"]
