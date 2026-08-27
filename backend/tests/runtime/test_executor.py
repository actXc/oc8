from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy import text as sql_text

from oc8 import models as m
from oc8.agent.engine import RunResult
from oc8.capas.lifecycle import enable_plugin
from oc8.capas.service import install_plugin
from oc8.constants import ACME_TENANT_ID
from oc8.runtime.executor import execute_run
from oc8.runtime.queue import RunMessage
from oc8.runtime.repository import RunRepository
from oc8.runtime.run_context import append_resolved_approval, append_tool_call
from oc8.runtime.states import RunState
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


class _FnRuntime:
    """Wraps a bare async(db, **kw) -> RunResult callable as a RuntimeAdapter,
    for tests that inject a scripted result directly (bypassing resolve_runtime)."""

    def __init__(self, fn: Callable[..., Awaitable[RunResult]]) -> None:
        self._fn = fn

    async def execute(self, db: Any, **kw: Any) -> RunResult:
        return await self._fn(db, **kw)


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


async def _make_queued_run(app_session: AppSessionFactory, tenant: uuid.UUID) -> uuid.UUID:
    async with app_session(tenant) as s:
        agent = m.Agent(
            tenant_id=tenant,
            department_id=uuid.uuid4(),
            name="Dev",
        )
        s.add(agent)
        await s.flush()
        repo = RunRepository(s)
        run = await repo.create(tenant_id=tenant, agent_id=agent.id, context={"task": "do it"})
        return run.id


async def test_execute_run_marks_done(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    run_id = await _make_queued_run(app_session, tenant)

    async def fake_runner(db: Any, **kw: Any) -> RunResult:
        return RunResult(
            task_id=uuid.uuid4(),
            agent_id=kw["agent"].id,
            status="done",
            output="finished",
            tool_calls=[],
            steps=2,
        )

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False),
        runtime=_FnRuntime(fake_runner),
    )

    async with app_session(tenant) as s:
        run = await s.get(m.AgentRun, run_id)
        assert run is not None
        assert run.state == RunState.DONE.value
        assert run.context["output"] == "finished"
        assert run.context["steps"] == 2
        # A finished run frees the agent back to idle for the office view.
        agent = await s.get(m.Agent, run.agent_id)
        assert agent is not None
        assert agent.status == "idle"


async def test_execute_run_marks_failed_on_error(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    run_id = await _make_queued_run(app_session, tenant)

    async def boom(db: Any, **kw: Any) -> RunResult:
        raise RuntimeError("kaboom")

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False),
        runtime=_FnRuntime(boom),
    )

    async with app_session(tenant) as s:
        run = await s.get(m.AgentRun, run_id)
        assert run is not None
        assert run.state == RunState.FAILED.value
        assert "kaboom" in run.context["error"]
        # A failed run must leave a trace an operator can actually see --
        # previously this was silent everywhere (Activity, agent Overview).
        events = (
            (
                await s.execute(
                    select(m.ActivityEvent).where(
                        m.ActivityEvent.agent_id == run.agent_id,
                        m.ActivityEvent.status == "error",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert "kaboom" in (events[0].detail or "")


async def test_execute_run_marks_failed_when_agent_missing(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as s:
        repo = RunRepository(s)
        created = await repo.create(
            tenant_id=tenant, agent_id=uuid.uuid4(), context={"task": "do it"}
        )
        run_id = created.id

    async def unused_runner(db: Any, **kw: Any) -> RunResult:
        raise AssertionError("runner should not be called when agent is missing")

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False),
        runtime=_FnRuntime(unused_runner),
    )

    async with app_session(tenant) as s:
        run = await s.get(m.AgentRun, run_id)
        assert run is not None
        assert run.state == RunState.FAILED.value
        assert "agent not found" in run.context["error"]
        events = (
            (
                await s.execute(
                    select(m.ActivityEvent).where(
                        m.ActivityEvent.agent_id == run.agent_id,
                        m.ActivityEvent.status == "error",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert "agent not found" in events[0].message


async def test_execute_run_marks_failed_on_unknown_mcp_connection(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    unknown_mcp_id = uuid.uuid4()
    async with app_session(tenant) as s:
        agent = m.Agent(
            tenant_id=tenant,
            department_id=uuid.uuid4(),
            name="Dev",
        )
        s.add(agent)
        await s.flush()
        repo = RunRepository(s)
        created = await repo.create(
            tenant_id=tenant,
            agent_id=agent.id,
            context={"task": "do it", "mcp_connection_id": str(unknown_mcp_id)},
        )
        run_id = created.id

    async def unused_runner(db: Any, **kw: Any) -> RunResult:
        raise AssertionError("runner should not be called for an unknown MCP connection")

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False),
        runtime=_FnRuntime(unused_runner),
    )

    async with app_session(tenant) as s:
        run = await s.get(m.AgentRun, run_id)
        assert run is not None
        assert run.state == RunState.FAILED.value
        assert "unknown MCP connection" in run.context["error"]
        events = (
            (
                await s.execute(
                    select(m.ActivityEvent).where(
                        m.ActivityEvent.agent_id == run.agent_id,
                        m.ActivityEvent.status == "error",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert "unknown MCP connection" in events[0].message


async def test_execute_run_marks_failed_on_unknown_status(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    run_id = await _make_queued_run(app_session, tenant)

    async def weird_runner(db: Any, **kw: Any) -> RunResult:
        return RunResult(
            task_id=uuid.uuid4(),
            agent_id=kw["agent"].id,
            status="weird",
            output="huh",
            tool_calls=[],
            steps=1,
        )

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False),
        runtime=_FnRuntime(weird_runner),
    )

    async with app_session(tenant) as s:
        run = await s.get(m.AgentRun, run_id)
        assert run is not None
        assert run.state == RunState.FAILED.value
        assert "unknown run status" in run.context["error"]
        events = (
            (
                await s.execute(
                    select(m.ActivityEvent).where(
                        m.ActivityEvent.agent_id == run.agent_id,
                        m.ActivityEvent.status == "error",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert "unknown run status" in events[0].message


async def test_execute_run_marks_waiting_for_approval_on_budget_exceeded(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    run_id = await _make_queued_run(app_session, tenant)

    async def budget_runner(db: Any, **kw: Any) -> RunResult:
        # Mirrors what run_agent does on a hard budget-exceeded stop: it
        # pauses the agent itself before returning the RunResult.
        kw["agent"].status = "paused"
        return RunResult(
            task_id=uuid.uuid4(),
            agent_id=kw["agent"].id,
            status="budget_exceeded",
            output="budget exceeded",
            tool_calls=[],
            steps=1,
        )

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False),
        runtime=_FnRuntime(budget_runner),
    )

    async with app_session(tenant) as s:
        run = await s.get(m.AgentRun, run_id)
        assert run is not None
        # budget_exceeded is resumable, not a terminal failure.
        assert run.state == RunState.WAITING_FOR_APPROVAL.value
        assert "error" not in run.context
        # The agent's "paused" status (set by run_agent) must survive, not
        # get clobbered back to "idle" by the executor.
        agent = await s.get(m.Agent, run.agent_id)
        assert agent is not None
        assert agent.status == "paused"


async def test_execute_run_dispatches_to_resolved_stub_runtime(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as s:
        # Unique semver suffix: ACME_TENANT_ID is shared across the whole
        # suite, and install_plugin raises DuplicateVersionError on a
        # repeated (plugin_id, semver) for the same plugin name — see the
        # identical note in tests/runtime/test_registry.py's
        # _install_stub_runtime helper.
        version = await install_plugin(
            s,
            tenant_id=tenant,
            manifest_data={
                "name": "oc8.echo-runtime-stub",
                "version": f"1.0.0+{uuid.uuid4().hex[:8]}",
                "type": "runtime_adapter",
                "trust": "community",
                "capabilities": ["streaming"],
            },
        )
        await enable_plugin(s, tenant_id=tenant, capa_id=version.capa_id, granted_permissions=[])
        agent = m.Agent(
            tenant_id=tenant,
            department_id=uuid.uuid4(),
            name="Dev",
            runtime_ref=str(version.capa_id),
        )
        s.add(agent)
        await s.flush()
        repo = RunRepository(s)
        run = await repo.create(tenant_id=tenant, agent_id=agent.id, context={"task": "hi"})
        run_id = run.id

    # No `runtime=` override: exercises the real resolve_runtime path.
    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False)
    )

    async with app_session(tenant) as s:
        refreshed = await s.get(m.AgentRun, run_id)
        assert refreshed is not None
        assert refreshed.state == RunState.DONE.value
        assert "echo-runtime-stub" in refreshed.context["output"]


async def test_execute_run_marks_failed_on_stale_runtime_ref(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as s:
        agent = m.Agent(
            tenant_id=tenant,
            department_id=uuid.uuid4(),
            name="Dev",
            runtime_ref=str(uuid.uuid4()),
        )
        s.add(agent)
        await s.flush()
        repo = RunRepository(s)
        run = await repo.create(tenant_id=tenant, agent_id=agent.id, context={"task": "hi"})
        run_id = run.id

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False)
    )

    async with app_session(tenant) as s:
        refreshed = await s.get(m.AgentRun, run_id)
        assert refreshed is not None
        assert refreshed.state == RunState.FAILED.value
        assert "RuntimeResolutionError" in refreshed.context["error"]


async def test_execute_run_threads_parent_task_id_and_depth_from_context(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    parent_id = uuid.uuid4()
    async with app_session(tenant) as s:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="Dev")
        s.add(agent)
        await s.flush()
        repo = RunRepository(s)
        created = await repo.create(
            tenant_id=tenant,
            agent_id=agent.id,
            context={
                "task": "sub work",
                "parent_task_id": str(parent_id),
                "delegation_depth": 2,
            },
        )
        run_id = created.id

    seen: dict[str, Any] = {}

    async def capturing_runner(db: Any, **kw: Any) -> RunResult:
        seen.update(kw)
        return RunResult(
            task_id=uuid.uuid4(),
            agent_id=kw["agent"].id,
            status="done",
            output="ok",
            tool_calls=[],
            steps=1,
        )

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False),
        runtime=_FnRuntime(capturing_runner),
    )

    assert seen["parent_task_id"] == parent_id
    assert seen["delegation_depth"] == 2


class _VisibilityCheckingQueue:
    """Records enqueues, asserting at enqueue time that the run row is already
    durably visible from a brand-new session — i.e. the commit happened BEFORE
    the publish. The worker's XREADGROUP can return the instant a stream entry
    exists, so a row not yet committed would be silently dropped. Mirrors the
    guard in tests/api/test_run_endpoint.py."""

    def __init__(self, app_session: AppSessionFactory, tenant: uuid.UUID) -> None:
        self._app_session = app_session
        self._tenant = tenant
        self.enqueued: list[uuid.UUID] = []

    async def enqueue(self, *, run_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        async with self._app_session(self._tenant) as s:
            row = await s.get(m.AgentRun, run_id)
            assert row is not None, "run must be committed before it is published"
        self.enqueued.append(run_id)


async def test_execute_run_publishes_pending_runs_after_commit(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    run_id = await _make_queued_run(app_session, tenant)
    queue = _VisibilityCheckingQueue(app_session, tenant)
    monkeypatch.setattr("oc8.runtime.intake.get_run_queue", lambda: queue)

    async def delegating_runner(db: Any, **kw: Any) -> RunResult:
        sub = await RunRepository(db).create(
            tenant_id=kw["tenant_id"],
            agent_id=kw["agent"].id,
            context={"task": "sub"},
            source="delegation",
        )
        return RunResult(
            task_id=uuid.uuid4(),
            agent_id=kw["agent"].id,
            status="done",
            output="delegated",
            tool_calls=[],
            steps=1,
            pending_runs=[sub.id],
        )

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False),
        runtime=_FnRuntime(delegating_runner),
    )

    assert len(queue.enqueued) == 1
    async with app_session(tenant) as s:
        sub = await s.get(m.AgentRun, queue.enqueued[0])
        assert sub is not None
        assert sub.state == RunState.QUEUED.value


async def test_execute_run_publishes_nothing_when_no_pending_runs(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    run_id = await _make_queued_run(app_session, tenant)
    queue = _VisibilityCheckingQueue(app_session, tenant)
    monkeypatch.setattr("oc8.runtime.intake.get_run_queue", lambda: queue)

    async def plain_runner(db: Any, **kw: Any) -> RunResult:
        return RunResult(
            task_id=uuid.uuid4(),
            agent_id=kw["agent"].id,
            status="done",
            output="ok",
            tool_calls=[],
            steps=1,
        )

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False),
        runtime=_FnRuntime(plain_runner),
    )

    assert queue.enqueued == []


async def test_execute_run_interrupts_when_cancel_requested_mid_flight(
    app_session: AppSessionFactory,
) -> None:
    """A run_cancellation row inserted by a SEPARATE committed session (as the
    cancel endpoint does) is seen by the executor's own long transaction under
    READ COMMITTED, and the run ends INTERRUPTED with the agent idle. Because the
    signal lives off the locked agent_run tuple, this does NOT deadlock (the old
    cancel_requested_at column, on the agent_run row the executor holds locked,
    did)."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    run_id = await _make_queued_run(app_session, tenant)

    async def cancelling_runner(db: Any, **kw: Any) -> RunResult:
        check = kw["cancel_check"]
        assert await check() is False
        # A different committed session records the cancel mid-run.
        async with app_session(tenant) as other:
            other.add(
                m.RunCancellation(
                    tenant_id=tenant,
                    run_id=run_id,
                    requested_at=_now(),
                    cancellation_kind="operator_interrupted",
                )
            )
        assert await check() is True
        return RunResult(
            task_id=uuid.uuid4(),
            agent_id=kw["agent"].id,
            status="interrupted",
            output="cancelled",
            tool_calls=[],
            steps=1,
        )

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False),
        runtime=_FnRuntime(cancelling_runner),
    )

    async with app_session(tenant) as s:
        run = await s.get(m.AgentRun, run_id)
        assert run is not None
        assert run.state == RunState.INTERRUPTED.value
        assert run.context["cancellation_kind"] == "operator_interrupted"
        agent = await s.get(m.Agent, run.agent_id)
        assert agent is not None and agent.status == "idle"


async def test_execute_run_skips_a_run_cancelled_while_queued(
    app_session: AppSessionFactory,
) -> None:
    """Cancelled while still queued: execute_run honors the run_cancellation row
    BEFORE invoking the runtime -> the run ends INTERRUPTED, the agent is idle,
    and the runtime is never called (the stream entry acks on the normal return
    -> drained)."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    run_id = await _make_queued_run(app_session, tenant)
    async with app_session(tenant) as s:
        s.add(
            m.RunCancellation(
                tenant_id=tenant,
                run_id=run_id,
                requested_at=_now(),
                cancellation_kind="operator_interrupted",
            )
        )

    async def must_not_run(db: Any, **kw: Any) -> RunResult:
        raise AssertionError("a cancelled queued run must not invoke the runtime")

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False),
        runtime=_FnRuntime(must_not_run),
    )

    async with app_session(tenant) as s:
        run = await s.get(m.AgentRun, run_id)
        assert run is not None and run.state == RunState.INTERRUPTED.value
        assert run.context["cancellation_kind"] == "operator_interrupted"
        agent = await s.get(m.Agent, run.agent_id)
        assert agent is not None and agent.status == "idle"


async def test_a_decision_committed_while_the_run_finishes_survives(
    app_session: AppSessionFactory,
) -> None:
    """The operator approves while the run is still going, and the executor's own
    write must not erase that.

    Found by review during the runtime-hardening slice, never reproduced in the
    wild because it needs the decision to land inside a window of milliseconds:
    the executor refetches the run when the adapter returns, then flushes
    `{**run.context, output/steps}` built from THAT snapshot. Anything committed
    to the same JSONB column in between -- `resolved_tool_approvals` is the one
    that matters -- is silently written back out. The operator sees "approved",
    and nothing ever happens. `run_cancellation` and `run_message` are side
    tables precisely to dodge this; the approvals list never got that treatment.
    """
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    run_id = await _make_queued_run(app_session, tenant)
    entry = {"sig": "create_record\n{}", "tool": "create_record", "decision": "approve"}

    async def fake_runner(db: Any, **kw: Any) -> RunResult:
        # Release the executor's row lock first -- both real container runtimes
        # commit here for exactly this reason, and without it the concurrent
        # write below simply blocks on us and the deadlock hides the bug. The
        # GUC is transaction-local, so re-pin it exactly as they do; RLS fails
        # closed otherwise and the failure looks nothing like this test's point.
        await db.commit()
        await db.execute(
            sql_text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant)}
        )
        # A SEPARATE session, committing mid-run: what the approvals endpoint
        # does while a long-lived runtime is still driving the container.
        async with app_session(tenant) as api_db:
            run = await api_db.get(m.AgentRun, run_id)
            assert run is not None
            await append_resolved_approval(api_db, run, entry)
            await api_db.commit()
        return RunResult(
            task_id=uuid.uuid4(),
            agent_id=kw["agent"].id,
            status="done",
            output="finished",
            tool_calls=[],
            steps=1,
        )

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False),
        runtime=_FnRuntime(fake_runner),
    )

    async with app_session(tenant) as s:
        run = await s.get(m.AgentRun, run_id)
        assert run is not None
        assert run.context["output"] == "finished", "the executor's own write must still land"
        assert run.context["resolved_tool_approvals"] == [entry], "the decision was overwritten"


async def test_append_tool_call_is_visible_before_the_run_finishes(
    app_session: AppSessionFactory,
) -> None:
    """The actual bug (live-observed 2026-08-25): a run's `toolCalls` never
    appeared in `agent_run.context` until the whole run ended -- a browser
    refresh mid-run showed an empty list even ten steps in, because the
    only write was executor.py's own merge_context() call after run_agent()
    already returned everything. append_tool_call lets the in-process
    engine persist each call as it happens instead."""
    tenant = uuid.uuid4()
    run_id = await _make_queued_run(app_session, tenant)
    first = {"tool": "search_record", "arguments": {"model": "res.partner"}, "result": "3 found"}
    second = {"tool": "get_record", "arguments": {"model": "res.partner", "id": 3}, "result": "ok"}

    async with app_session(tenant) as s:
        run = await s.get(m.AgentRun, run_id)
        assert run is not None
        await append_tool_call(s, run, first)
        await s.commit()

    # A page load between steps -- a FRESH session, the same as a real
    # browser's GET /runs/{id} would use -- must already see the first call.
    async with app_session(tenant) as s:
        run = await s.get(m.AgentRun, run_id)
        assert run is not None
        assert run.context.get("toolCalls") == [first], (
            "a mid-run read must see the tool call already, not just the final one"
        )

    async with app_session(tenant) as s:
        run = await s.get(m.AgentRun, run_id)
        assert run is not None
        await append_tool_call(s, run, second)
        await s.commit()

    async with app_session(tenant) as s:
        run = await s.get(m.AgentRun, run_id)
        assert run is not None
        assert run.context.get("toolCalls") == [first, second], (
            "appends accumulate in order -- neither call overwrites the other"
        )


async def test_append_tool_call_survives_the_executors_final_write(
    app_session: AppSessionFactory,
) -> None:
    """The incremental appends during the run must not be erased by
    executor.py's own merge_context({"toolCalls": result.tool_calls, ...})
    once run_agent() returns -- that write is still the authoritative one
    for "output"/"steps", but it must not regress toolCalls to a stale
    snapshot the runtime captured before the last append landed."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    run_id = await _make_queued_run(app_session, tenant)
    live_call = {"tool": "search_record", "arguments": {}, "result": "ok"}

    async def fake_runner(db: Any, **kw: Any) -> RunResult:
        # Same session `execute_run` is already using -- exactly how the
        # real engine.py's _live_tool_call does it (append_tool_call(db, ...)
        # on the one session threaded through run_agent, never a second
        # session). A second `app_session(tenant)` opened HERE, nested
        # inside execute_run's own still-open transaction on this same
        # agent_run row, deadlocks against it instead of testing anything
        # real -- this is a test-construction bug, not a production one.
        run = await db.get(m.AgentRun, run_id)
        assert run is not None
        await append_tool_call(db, run, live_call)
        return RunResult(
            task_id=uuid.uuid4(),
            agent_id=kw["agent"].id,
            status="done",
            output="finished",
            tool_calls=[live_call],
            steps=1,
        )

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False),
        runtime=_FnRuntime(fake_runner),
    )

    async with app_session(tenant) as s:
        run = await s.get(m.AgentRun, run_id)
        assert run is not None
        assert run.context["toolCalls"] == [live_call]
        assert run.context["output"] == "finished"
