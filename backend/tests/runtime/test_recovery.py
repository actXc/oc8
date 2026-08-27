from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from sqlalchemy import update

from oc8 import models as m
from oc8.runtime.executor import recover_reclaimed
from oc8.runtime.queue import RunMessage
from oc8.runtime.reconcile import ABANDONED_AFTER
from oc8.runtime.repository import RunRepository
from oc8.runtime.states import RunState
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _reclaimed(run_id: uuid.UUID, tenant_id: uuid.UUID) -> RunMessage:
    return RunMessage(
        run_id=str(run_id),
        tenant_id=str(tenant_id),
        entry_id="0-0",
        redelivered=True,
    )


async def _last_beat(db: Any, run_id: uuid.UUID, *, ago: dt.timedelta) -> None:
    """Put this run's last heartbeat `ago` in the past.

    `updated_at` IS the heartbeat -- reconcile.heartbeat writes nothing else --
    so this is what a worker that stopped reporting `ago` ago looks like. Written
    with an explicit value, since the column's onupdate would otherwise stamp
    `now()` over it.
    """
    await db.execute(
        update(m.AgentRun)
        .where(m.AgentRun.id == run_id)
        .values(updated_at=dt.datetime.now(tz=dt.UTC) - ago)
    )


async def test_reclaimed_queued_run_executes(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        repo = RunRepository(s)
        # Bare agent id: execute_run will fail it (agent not found), which is
        # enough to prove the queued branch drove it through the normal path.
        run = await repo.create(
            tenant_id=tenant, agent_id=uuid.uuid4(), context={"task": "do it"}
        )
        run_id = run.id

    await recover_reclaimed(_reclaimed(run_id, tenant))

    async with app_session(tenant) as s:
        refreshed = await s.get(m.AgentRun, run_id)
        assert refreshed is not None
        # It left `queued` — execute_run took over and drove it.
        assert refreshed.state != RunState.QUEUED.value


async def test_reclaimed_running_run_fails_with_lease_lost(
    app_session: AppSessionFactory,
) -> None:
    """A run whose entry was reclaimed AND whose heartbeat stopped: two facts
    that agree, so it is safe to call the worker dead. (The heartbeat is aged
    here, where it used to be fresh -- the fixture, not the assertion, is what
    the second fact changed.)"""
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        agent = m.Agent(
            tenant_id=tenant,
            department_id=uuid.uuid4(),
            name="Dev",
            status="running",
        )
        s.add(agent)
        await s.flush()
        repo = RunRepository(s)
        run = await repo.create(
            tenant_id=tenant, agent_id=agent.id, context={"task": "do it"}
        )
        await repo.transition(run, RunState.RUNNING)
        run_id, agent_id = run.id, agent.id
        await _last_beat(s, run_id, ago=ABANDONED_AFTER * 2)

    await recover_reclaimed(_reclaimed(run_id, tenant))

    async with app_session(tenant) as s:
        refreshed = await s.get(m.AgentRun, run_id)
        assert refreshed is not None
        assert refreshed.state == RunState.FAILED.value
        assert "lease lost" in refreshed.context["error"]
        refreshed_agent = await s.get(m.Agent, agent_id)
        assert refreshed_agent is not None
        assert refreshed_agent.status == "idle"


async def test_a_reclaimed_run_that_is_still_beating_is_left_to_its_executor(
    app_session: AppSessionFactory,
) -> None:
    """THE SECOND FACT. A reclaim says a consumer stopped talking to Redis. It
    does NOT say the work stopped -- a worker whose Redis is unreachable for ten
    minutes looks exactly like this while its container works and its database
    heartbeat lands every thirty seconds. Failing that run as "lease lost" is
    run 019fc303 again with a longer fuse and a different trigger, and this
    function had no way to tell the two apart: it never asked the row.

    Now it asks. A heartbeat inside ABANDONED_AFTER means the executor is
    demonstrably alive, so the run is left alone -- state, error and agent all
    untouched -- and the reconciler remains its decider for when the beating
    really does stop.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="Nora", status="running")
        s.add(agent)
        await s.flush()
        repo = RunRepository(s)
        run = await repo.create(
            tenant_id=tenant, agent_id=agent.id, context={"task": "verkauf etwas"}
        )
        await repo.transition(run, RunState.RUNNING)
        run_id, agent_id = run.id, agent.id
        # Five minutes in and still beating: exactly where 019fc303 was killed.
        await _last_beat(s, run_id, ago=dt.timedelta(minutes=5))

    await recover_reclaimed(_reclaimed(run_id, tenant))

    async with app_session(tenant) as s:
        refreshed = await s.get(m.AgentRun, run_id)
        assert refreshed is not None
        assert refreshed.state == RunState.RUNNING.value
        assert "error" not in refreshed.context
        # And the agent is still working, because it is still working.
        still = await s.get(m.Agent, agent_id)
        assert still is not None
        assert still.status == "running"


async def test_reclaimed_terminal_run_is_untouched(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        repo = RunRepository(s)
        run = await repo.create(
            tenant_id=tenant, agent_id=uuid.uuid4(), context={"task": "done it"}
        )
        await repo.transition(run, RunState.RUNNING)
        await repo.transition(run, RunState.DONE)
        run_id = run.id

    await recover_reclaimed(_reclaimed(run_id, tenant))

    async with app_session(tenant) as s:
        refreshed = await s.get(m.AgentRun, run_id)
        assert refreshed is not None
        # A terminal run is a duplicate delivery: state and context untouched.
        assert refreshed.state == RunState.DONE.value
        assert refreshed.context == {"task": "done it"}


async def test_reclaimed_missing_run_is_noop() -> None:
    # A random run_id/tenant must not raise.
    await recover_reclaimed(_reclaimed(uuid.uuid4(), uuid.uuid4()))
