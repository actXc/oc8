"""GET /reports -- finished runs that rendered at least one component
(chart/table/record card), the "My work" Reports list reads this."""

from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role="org_admin")
    return {"Authorization": f"Bearer {token}"}


async def _seed_agent(app_session: AppSessionFactory, tenant: uuid.UUID, name: str) -> uuid.UUID:
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Finance", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name=name)
        db.add(agent)
        await db.flush()
        return agent.id


async def _seed_run(
    app_session: AppSessionFactory,
    tenant: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    state: str,
    rendered_components: list[dict[str, object]] | None,
) -> uuid.UUID:
    async with app_session(tenant) as db:
        context: dict[str, object] = {"task": "daily report"}
        if rendered_components is not None:
            context["rendered_components"] = rendered_components
        run = m.AgentRun(tenant_id=tenant, agent_id=agent_id, state=state, context=context)
        db.add(run)
        await db.flush()
        return run.id


_CHART = {
    "componentKey": "bar_chart",
    "props": {"title": "Hours", "labels": ["Mon"], "series": [{"name": "A", "values": [1.0]}]},
}


async def test_done_run_with_a_component_is_listed(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    agent_id = await _seed_agent(app_session, tenant, "Reporter")
    run_id = await _seed_run(
        app_session, tenant, agent_id, state="done", rendered_components=[_CHART]
    )

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/reports", headers=_headers(tenant))
            assert r.status_code == 200, r.text
            body = r.json()
            assert len(body) == 1
            assert body[0]["runId"] == str(run_id)
            assert body[0]["agentName"] == "Reporter"
            assert body[0]["renderedComponents"] == [_CHART]


async def test_done_run_without_a_component_is_not_listed(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    agent_id = await _seed_agent(app_session, tenant, "Silent")
    await _seed_run(app_session, tenant, agent_id, state="done", rendered_components=None)
    await _seed_run(app_session, tenant, agent_id, state="done", rendered_components=[])

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/reports", headers=_headers(tenant))
            assert r.status_code == 200, r.text
            assert r.json() == []


async def test_non_terminal_run_with_a_component_is_not_listed(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent_id = await _seed_agent(app_session, tenant, "Running")
    await _seed_run(app_session, tenant, agent_id, state="running", rendered_components=[_CHART])

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/reports", headers=_headers(tenant))
            assert r.status_code == 200, r.text
            assert r.json() == []


async def test_agent_id_filter_narrows_to_one_agent(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    a1 = await _seed_agent(app_session, tenant, "A1")
    a2 = await _seed_agent(app_session, tenant, "A2")
    run1 = await _seed_run(app_session, tenant, a1, state="done", rendered_components=[_CHART])
    await _seed_run(app_session, tenant, a2, state="done", rendered_components=[_CHART])

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get(f"/api/v1/reports?agentId={a1}", headers=_headers(tenant))
            assert r.status_code == 200, r.text
            body = r.json()
            assert len(body) == 1
            assert body[0]["runId"] == str(run1)
