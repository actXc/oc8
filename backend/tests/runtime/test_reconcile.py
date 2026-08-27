"""A run whose executor has gone silent has to be closed.

Live, 2026-07-28: nine runs marked running with two containers alive, three of
the rows five hours old. The reclaim path only covers a worker killed with its
stream entry unacked; when the entry was acked and the work died anyway, nobody
ever looks at that run again. The agent then reads as permanently busy on its
own board, and the failure reaches no one.

Liveness is REPORTED, not inferred from the local Docker daemon: that is right
on one host and wrong on two, where a second worker sees none of the first
host's containers and would declare its live runs dead.

The dangerous half is not leaving a dead run open — it is closing a live one, so
every test here is really about what must be LEFT ALONE.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.runtime.reconcile import close_abandoned_runs, heartbeat
from tests.conftest import AppSessionFactory

# The sweep is global by design -- it discovers every tenant, exactly as the cron
# scheduler does. So its RESULT contains whatever other tests happened to leave
# open, and every assertion here is about this test's own run, never the list.

pytestmark = pytest.mark.asyncio

LONG_AGO = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)


async def _run(db: Any, tenant: uuid.UUID, *, state: str = "running") -> m.AgentRun:
    # A real Organization row: the sweep discovers tenants exactly the way the
    # cron scheduler does, so a tenant with no row is invisible to it.
    if await db.get(m.Organization, tenant) is None:
        db.add(m.Organization(id=tenant, slug=f"t-{tenant.hex[:8]}", name="T"))
        await db.flush()
    dept = m.Department(tenant_id=tenant, name="Kundenservice", frame={})
    db.add(dept)
    await db.flush()
    agent = m.Agent(
        tenant_id=tenant, department_id=dept.id, name="Sina", status="running",
        narrowing={}, definition={}, presentation={},
    )
    db.add(agent)
    await db.flush()
    run = m.AgentRun(
        tenant_id=tenant, agent_id=agent.id, state=state, context={"task": "x"}
    )
    db.add(run)
    await db.flush()
    return run


async def _age(db: Any, run: m.AgentRun, when: dt.datetime) -> None:
    """Backdate the row the way five hours of neglect would."""
    from sqlalchemy import update

    await db.execute(
        update(m.AgentRun).where(m.AgentRun.id == run.id).values(updated_at=when)
    )
    await db.commit()


async def test_a_run_that_stopped_reporting_in_is_closed(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        run_id = run.id
        await _age(db, run, LONG_AGO)

    closed = await close_abandoned_runs()
    assert run_id in closed

    async with app_session(tenant) as db:
        after = await db.get(m.AgentRun, run_id)
        assert after is not None
        assert after.state == "failed"
        assert "abandoned" in after.context["error"]


async def test_the_agent_is_freed(app_session: AppSessionFactory) -> None:
    """The point of closing it: while the run reads as running the office view
    lies about what the agent is doing."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        agent_id = run.agent_id
        await _age(db, run, LONG_AGO)

    await close_abandoned_runs()

    async with app_session(tenant) as db:
        agent = await db.get(m.Agent, agent_id)
        assert agent is not None
        assert agent.status == "idle"


async def test_a_run_that_is_still_beating_is_left_alone(
    app_session: AppSessionFactory,
) -> None:
    """Slow is not dead. An agent that legitimately works for hours keeps
    working — the heartbeat is what says so, and it says it wherever the run
    happens to be executing rather than only on this host."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        run_id = run.id
        await _age(db, run, dt.datetime.now(tz=dt.UTC))

    assert run_id not in await close_abandoned_runs()

    async with app_session(tenant) as db:
        after = await db.get(m.AgentRun, run_id)
        assert after is not None
        assert after.state == "running"


async def test_a_recently_touched_run_is_left_alone(
    app_session: AppSessionFactory,
) -> None:
    """A run that has just been claimed has not started its container yet. The
    age window is what stops the sweep racing the start of every run."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        run_id = run.id
        await db.commit()

    assert run_id not in await close_abandoned_runs()

    async with app_session(tenant) as db:
        after = await db.get(m.AgentRun, run_id)
        assert after is not None
        assert after.state == "running"


async def test_a_beat_rescues_a_run_that_had_gone_quiet(
    app_session: AppSessionFactory,
) -> None:
    """The heartbeat is what the sweep reads, so a single beat is enough to
    make a run that looked abandoned safe again."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        run_id = run.id
        await _age(db, run, LONG_AGO)

    # A SECOND session for the beat: `_age` commits, and the tenant binding is
    # transaction-local, so a further statement on the same session runs unbound
    # -- where RLS fails closed and the UPDATE silently touches nothing. The
    # same trap the runtime hits, met here in a test.
    async with app_session(tenant) as db:
        again = await db.get(m.AgentRun, run_id)
        assert again is not None
        await _age(db, again, dt.datetime.now(tz=dt.UTC))  # one beat

    assert run_id not in await close_abandoned_runs()

    async with app_session(tenant) as db:
        after = await db.get(m.AgentRun, run_id)
        assert after is not None
        assert after.state == "running"


@pytest.mark.parametrize("state", ["queued", "done", "failed", "waiting_for_approval"])
async def test_only_running_runs_are_swept(
    app_session: AppSessionFactory, state: str
) -> None:
    """A queued run has not started, and a waiting one is waiting on a HUMAN —
    failing either would destroy work that is merely paused."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant, state=state)
        run_id = run.id
        await _age(db, run, LONG_AGO)

    assert run_id not in await close_abandoned_runs()

    async with app_session(tenant) as db:
        after = await db.get(m.AgentRun, run_id)
        assert after is not None
        assert after.state == state


async def test_the_heartbeat_keeps_a_long_run_out_of_the_sweep(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole premise: liveness is REPORTED, so a run that legitimately takes
    hours stays untouched while it reports. Without the beat this same run is
    swept — which is exactly what the assertion after the block shows."""
    monkeypatch.setattr("oc8.runtime.reconcile.HEARTBEAT_SECONDS", 0.05)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        run_id = run.id
        await _age(db, run, LONG_AGO)

    async with heartbeat(tenant_id=tenant, run_id=run_id):
        await asyncio.sleep(0.3)  # several beats
        assert run_id not in await close_abandoned_runs()

    # Beating has stopped. Age it out again and the sweep takes it.
    async with app_session(tenant) as db:
        again = await db.get(m.AgentRun, run_id)
        assert again is not None
        await _age(db, again, LONG_AGO)
    assert run_id in await close_abandoned_runs()


async def test_a_failing_beat_never_escapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One missed beat is not a death, and a database hiccup must never take
    down the run it was reporting on."""
    monkeypatch.setattr("oc8.runtime.reconcile.HEARTBEAT_SECONDS", 0.05)

    def _boom(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("database went away")

    monkeypatch.setattr("oc8.runtime.reconcile.tenant_session", _boom)
    async with heartbeat(tenant_id=uuid.uuid4(), run_id=uuid.uuid4()):
        await asyncio.sleep(0.2)


async def test_a_run_closed_underneath_its_worker_does_not_crash_it(
    app_session: AppSessionFactory,
) -> None:
    """The reconciler can close a run whose heartbeat lapsed while its worker is
    still inside the runtime call. When that worker returns and marks the run
    failed, the transition is failed -> failed — which raised straight out of
    the worker loop, left the message unacked, and had it redelivered for ever.
    Two writers who AGREE are not a race worth failing on."""
    from oc8.runtime.repository import RunRepository
    from oc8.runtime.states import RunState

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        run_id = run.id
        await _age(db, run, LONG_AGO)

    assert run_id in await close_abandoned_runs()

    async with app_session(tenant) as db:
        late = await db.get(m.AgentRun, run_id)
        assert late is not None
        await RunRepository(db).transition(late, RunState.FAILED)  # must not raise
        assert late.state == "failed"


async def test_a_run_another_closer_is_already_holding_is_left_to_them(
    app_session: AppSessionFactory,
) -> None:
    """The sweep and the queue's reclaim are now two deciders on ONE window.

    Until 2026-08-02 the reclaim fired at five minutes and this sweep at ten, so
    the reclaim always won and they never met. Since the reclaim moved to the
    same window they routinely reach the same run in the same second, from two
    processes -- and this sweep read its work list without a lock. Both saw
    `running`, both wrote, and the run was counted failed twice with the second
    error message erasing the first.

    Selecting the work list FOR UPDATE SKIP LOCKED settles it without a
    conversation: a run somebody else already holds needs nothing from us. The
    old unlocked read did not merely double-write, it BLOCKED -- the sweep sat on
    the other closer's row lock inside `merge_context` -- which is why this test
    is written with a wait: before the fix it does not fail, it hangs.
    """
    from sqlalchemy import select

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        run_id = run.id
        await _age(db, run, LONG_AGO)

    async with app_session(tenant) as holder:
        # Exactly what the other closer holds when it is midway through closing
        # this run: the row, locked, in an uncommitted transaction.
        await holder.execute(
            select(m.AgentRun).where(m.AgentRun.id == run_id).with_for_update()
        )

        closed = await asyncio.wait_for(close_abandoned_runs(), timeout=20.0)
        assert run_id not in closed

    # And nothing was written to it by us -- the other closer's verdict stands,
    # whatever it turns out to be.
    async with app_session(tenant) as db:
        after = await db.get(m.AgentRun, run_id)
        assert after is not None
        assert after.state == "running"
