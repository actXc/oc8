"""POST /runs/{id}/message — operator->agent live steering. The endpoint records
an append-only run_message row; the executor injects it at the next step."""

from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from oc8.runtime.states import RunState
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _h(tenant: uuid.UUID, role: str = "org_admin") -> dict[str, str]:
    tok = get_identity_provider().mint(tenant_id=tenant, subject="op", role=role)
    return {"Authorization": f"Bearer {tok}"}


async def _run(session: AppSessionFactory, tenant: uuid.UUID, state: RunState) -> uuid.UUID:
    async with session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="D", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant, department_id=dept.id, name="A", status="idle",
            narrowing={}, definition={},
        )
        db.add(agent)
        await db.flush()
        run = m.AgentRun(tenant_id=tenant, agent_id=agent.id, state=state.value, context={})
        db.add(run)
        await db.flush()
        return run.id


async def test_message_is_recorded_for_a_running_run(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    run_id = await _run(app_session, tenant, RunState.RUNNING)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                f"/api/v1/runs/{run_id}/message",
                json={"body": "Welchen Lead bearbeitest du gerade?"},
                headers=_h(tenant),
            )
            assert r.status_code == 202, r.text
    async with app_session(tenant) as db:
        rows = (
            await db.execute(select(m.RunMessage).where(m.RunMessage.run_id == run_id))
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].delivered is False
        assert "Lead" in rows[0].body


async def test_empty_body_is_rejected(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    run_id = await _run(app_session, tenant, RunState.RUNNING)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                f"/api/v1/runs/{run_id}/message", json={"body": "   "}, headers=_h(tenant)
            )
            assert r.status_code == 400


async def test_message_to_a_finished_run_is_rejected(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    run_id = await _run(app_session, tenant, RunState.DONE)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                f"/api/v1/runs/{run_id}/message", json={"body": "hi"}, headers=_h(tenant)
            )
            assert r.status_code == 409


async def test_message_requires_operator_or_admin(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    run_id = await _run(app_session, tenant, RunState.RUNNING)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                f"/api/v1/runs/{run_id}/message",
                json={"body": "hi"},
                headers=_h(tenant, "member"),
            )
            assert r.status_code == 403


async def test_another_tenant_cannot_message_the_run(app_session: AppSessionFactory) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    run_id = await _run(app_session, tenant_a, RunState.RUNNING)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                f"/api/v1/runs/{run_id}/message", json={"body": "hi"}, headers=_h(tenant_b)
            )
            assert r.status_code == 404
