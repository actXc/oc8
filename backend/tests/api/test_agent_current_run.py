"""What an operator can see about a run they did not start themselves.

The agent detail screen showed a live log only for a run started from that same
browser tab -- the run id lived in React state. A run started by a cron trigger,
by another operator, or before a page reload was invisible while it happened:
the agent said "running" and the log stayed empty.
"""

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


def _h(tenant: uuid.UUID) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role="org_admin")
    return {"Authorization": f"Bearer {token}"}


async def _agent(db: object, tenant: uuid.UUID) -> uuid.UUID:
    agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="Sina")
    db.add(agent)  # type: ignore[attr-defined]
    await db.flush()  # type: ignore[attr-defined]
    return agent.id


async def _run(db: object, tenant: uuid.UUID, agent_id: uuid.UUID, state: str) -> uuid.UUID:
    run = m.AgentRun(
        tenant_id=tenant, agent_id=agent_id, state=state, context={"task": "t"}
    )
    db.add(run)  # type: ignore[attr-defined]
    await db.flush()  # type: ignore[attr-defined]
    return run.id


async def _get_agent(tenant: uuid.UUID, agent_id: uuid.UUID) -> dict:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get(f"/api/v1/agents/{agent_id}", headers=_h(tenant))
            assert r.status_code == 200, r.text
            return r.json()


async def test_a_running_run_is_reported_on_the_agent(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id = await _agent(db, tenant)
        run_id = await _run(db, tenant, agent_id, "running")
        await db.commit()

    assert (await _get_agent(tenant, agent_id))["currentRunId"] == str(run_id)


async def test_a_queued_run_counts_as_current(app_session: AppSessionFactory) -> None:
    """A cron fire is visible the moment it is queued -- an operator watching a
    schedule should see it coming, not only once a container is up."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id = await _agent(db, tenant)
        run_id = await _run(db, tenant, agent_id, "queued")
        await db.commit()

    assert (await _get_agent(tenant, agent_id))["currentRunId"] == str(run_id)


async def test_a_finished_run_is_not_reported(app_session: AppSessionFactory) -> None:
    """Otherwise the screen would show a stale transcript as if it were live."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id = await _agent(db, tenant)
        await _run(db, tenant, agent_id, "done")
        await db.commit()

    assert (await _get_agent(tenant, agent_id))["currentRunId"] is None


async def test_a_run_waiting_for_a_human_is_still_current(
    app_session: AppSessionFactory,
) -> None:
    """Parked for approval is exactly when an operator looks at the screen."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id = await _agent(db, tenant)
        run_id = await _run(db, tenant, agent_id, "waiting_for_approval")
        await db.commit()

    assert (await _get_agent(tenant, agent_id))["currentRunId"] == str(run_id)


async def test_the_newest_unfinished_run_wins(app_session: AppSessionFactory) -> None:
    """A run abandoned by a dead worker can sit in `running` forever; the one the
    operator wants to watch is the latest."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id = await _agent(db, tenant)
        await _run(db, tenant, agent_id, "running")
        newest = await _run(db, tenant, agent_id, "queued")
        await db.commit()

    assert (await _get_agent(tenant, agent_id))["currentRunId"] == str(newest)
