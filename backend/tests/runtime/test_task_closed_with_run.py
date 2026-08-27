"""A run that ends closes the task it was working.

Found 2026-07-30 on the live system: 66 tasks sitting in `in_progress` behind a
FAILED run, the oldest four days old, across all three agents. `close_abandoned_runs`
fails the run and frees the agent, and `executor` fails the run when the runtime
raises -- neither ever touched the task row. Only the in-process engine closed
tasks, so every run that went through a container runtime left one open.

The board then lies in the most expensive direction: work that nobody is doing
still reads as being done, and an operator (or a lead deciding what to hand out)
believes it.

`RunRepository.transition` is where this belongs for exactly the reason already
written there about record claims -- it is the single funnel every run passes
through on its way to an end, and anything released only on the happy path is
held for ever by a failed one.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.runtime.repository import RunRepository
from oc8.runtime.states import RunState
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def _setup(
    db: Any, tenant: uuid.UUID, *, task_state: str = "in_progress", run_state: str = "running"
) -> tuple[m.AgentRun, m.Task]:
    if await db.get(m.Organization, tenant) is None:
        db.add(m.Organization(id=tenant, slug=f"t-{tenant.hex[:8]}", name="T"))
        await db.flush()
    dept = m.Department(tenant_id=tenant, name="Kundenservice", frame={})
    db.add(dept)
    await db.flush()
    agent = m.Agent(
        tenant_id=tenant,
        department_id=dept.id,
        name="Sina",
        status="running",
        narrowing={},
        definition={},
        presentation={},
    )
    db.add(agent)
    await db.flush()
    task = m.Task(
        tenant_id=tenant,
        department_id=dept.id,
        assigned_agent_id=agent.id,
        title="Ticket 42 beantworten",
        state=task_state,
    )
    db.add(task)
    await db.flush()
    run = m.AgentRun(
        tenant_id=tenant, agent_id=agent.id, task_id=task.id, state=run_state, context={}
    )
    db.add(run)
    await db.flush()
    return run, task


async def test_a_failed_run_closes_its_task(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run, task = await _setup(db, tenant)

        await RunRepository(db).transition(run, RunState.FAILED)

        await db.refresh(task)
        assert task.state == "failed"


async def test_a_finished_run_closes_its_task_as_done(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run, task = await _setup(db, tenant)

        await RunRepository(db).transition(run, RunState.DONE)

        await db.refresh(task)
        assert task.state == "done"


async def test_a_cancelled_run_closes_its_task(app_session: AppSessionFactory) -> None:
    """An operator stopping a run stops the work. Leaving the task open would
    put it back on the board as if it were still being done."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run, task = await _setup(db, tenant)

        await RunRepository(db).transition(run, RunState.INTERRUPTED)

        await db.refresh(task)
        assert task.state == "failed"


async def test_a_parked_task_is_closed_when_its_run_dies(
    app_session: AppSessionFactory,
) -> None:
    """`waiting_for_approval` is not terminal, and a task waiting on an approval
    whose run no longer exists is precisely the stuck state -- nobody will ever
    decide it, and nothing will ever move it."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run, task = await _setup(db, tenant, task_state="waiting_for_approval")

        await RunRepository(db).transition(run, RunState.FAILED)

        await db.refresh(task)
        assert task.state == "failed"


async def test_a_more_specific_verdict_is_not_overwritten(
    app_session: AppSessionFactory,
) -> None:
    """The engine already says `budget_exceeded`, which is a REASON, not just an
    ending. Coarsening it to `failed` here would destroy the one word that tells
    an operator to raise a budget rather than debug an agent."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run, task = await _setup(db, tenant, task_state="budget_exceeded")

        await RunRepository(db).transition(run, RunState.FAILED)

        await db.refresh(task)
        assert task.state == "budget_exceeded"


async def test_a_task_the_engine_already_completed_stays_done(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run, task = await _setup(db, tenant, task_state="done")

        await RunRepository(db).transition(run, RunState.FAILED)

        await db.refresh(task)
        assert task.state == "done"


async def test_a_sibling_run_still_working_keeps_the_task_open(
    app_session: AppSessionFactory,
) -> None:
    """One task may have several runs. Closing it because ONE of them ended
    would take the task away from the one still doing it."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run, task = await _setup(db, tenant)
        sibling = m.AgentRun(
            tenant_id=tenant,
            agent_id=run.agent_id,
            task_id=task.id,
            state="running",
            context={},
        )
        db.add(sibling)
        await db.flush()

        await RunRepository(db).transition(run, RunState.FAILED)

        await db.refresh(task)
        assert task.state == "in_progress"


async def test_a_run_without_a_task_is_a_no_op(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run, _task = await _setup(db, tenant)
        run.task_id = None
        await db.flush()

        await RunRepository(db).transition(run, RunState.FAILED)  # must not raise

        assert run.state == "failed"


async def test_the_reconciler_closes_the_task_it_abandons(
    app_session: AppSessionFactory,
) -> None:
    """End to end on the path that produced all 66: the sweep fails the run, and
    the task must not be left behind it."""
    import datetime as dt

    from sqlalchemy import update

    from oc8.runtime.reconcile import close_abandoned_runs

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run, task = await _setup(db, tenant)
        run_id, task_id = run.id, task.id
        await db.execute(
            update(m.AgentRun)
            .where(m.AgentRun.id == run_id)
            .values(updated_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC))
        )
        await db.commit()

    assert run_id in await close_abandoned_runs()

    async with app_session(tenant) as db:
        after = await db.get(m.Task, task_id)
        assert after is not None
        assert after.state == "failed"
