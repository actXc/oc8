from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _h(tenant: uuid.UUID, role: str) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="s", role=role)
    return {"Authorization": f"Bearer {token}"}


async def _make_run(db: AsyncSession, tenant: uuid.UUID, state: str) -> uuid.UUID:
    agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="A")
    db.add(agent)
    await db.flush()
    context: dict[str, object] = (
        {"pending_question": "q?", "task": "t"} if state == "waiting_for_input" else {"task": "t"}
    )
    run = m.AgentRun(tenant_id=tenant, agent_id=agent.id, state=state, context=context)
    db.add(run)
    # Flush now so run.id (a Python-side `default=uuid7`, only applied at
    # flush) is populated before it's read below -- constructing the
    # Clarification with run.id while it's still None would violate the
    # NOT NULL constraint on clarification.run_id.
    await db.flush()
    if state == "waiting_for_input":
        db.add(m.Clarification(tenant_id=tenant, run_id=run.id, agent_id=agent.id,
                               question="q?", status="open"))
    await db.flush()
    return run.id


async def test_operator_can_answer_a_waiting_run(
    app_session: AppSessionFactory, redis_url: str
) -> None:
    # redis_url must be REQUESTED, not merely available: answering re-queues the
    # run, and without the fixture that enqueue goes to whatever redis happens to
    # listen on the settings default. On a machine with none, the test fails with
    # a bare connection error that reads like a defect in the endpoint.
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run_id = await _make_run(db, tenant, "waiting_for_input")
        await db.commit()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(f"/api/v1/runs/{run_id}/answer", json={"answer": "use acct 1"},
                             headers=_h(tenant, "operator"))
            assert r.status_code == 200, r.text
            assert r.json()["state"] == "queued"


async def test_member_cannot_answer() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(f"/api/v1/runs/{uuid.uuid4()}/answer", json={"answer": "x"},
                             headers=_h(tenant, "member"))
            assert r.status_code == 403


async def test_answering_a_non_waiting_run_409(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run_id = await _make_run(db, tenant, "running")
        await db.commit()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(f"/api/v1/runs/{run_id}/answer", json={"answer": "x"},
                             headers=_h(tenant, "operator"))
            assert r.status_code == 409, r.text


async def test_answer_missing_run_404() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(f"/api/v1/runs/{uuid.uuid4()}/answer", json={"answer": "x"},
                             headers=_h(tenant, "operator"))
            assert r.status_code == 404
