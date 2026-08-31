"""Fixture-backed tests for `compute_kpis` -- one per sub-query.

Every test builds its own tenant UUID rather than reusing ACME: the KPI
sub-queries are tenant-wide AGGREGATES, so any row another test leaves behind
under a shared tenant would silently move an average.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.kpis.aggregate import compute_kpis
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

T0 = dt.datetime(2026, 8, 1, 12, 0, 0, tzinfo=dt.UTC)


def _sec(n: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=n)


def _run(
    tenant: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    created_at: dt.datetime = T0,
    context: dict[str, Any] | None = None,
    state: str = "done",
) -> m.AgentRun:
    return m.AgentRun(
        id=uuid.uuid4(),
        tenant_id=tenant,
        agent_id=agent_id,
        state=state,
        context=context or {},
        messages=[],
        created_at=created_at,
        updated_at=created_at,
    )


def _transition(
    tenant: uuid.UUID,
    run_id: uuid.UUID,
    from_state: str | None,
    to_state: str,
    at: dt.datetime,
) -> m.RunStateTransition:
    return m.RunStateTransition(
        id=uuid.uuid4(),
        tenant_id=tenant,
        run_id=run_id,
        from_state=from_state,
        to_state=to_state,
        at=at,
    )


async def test_run_count_scoped_to_one_agent(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    async with app_session(tenant) as db:
        db.add_all([_run(tenant, agent_a), _run(tenant, agent_a), _run(tenant, agent_b)])
        await db.flush()
        scoped = await compute_kpis(db, tenant_id=tenant, agent_id=agent_a)
        tenant_wide = await compute_kpis(db, tenant_id=tenant)
    assert scoped.run_count == 2
    assert tenant_wide.run_count == 3


async def test_total_duration_averages_across_runs_in_scope(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    async with app_session(tenant) as db:
        r1, r2 = _run(tenant, agent), _run(tenant, agent)
        db.add_all([r1, r2])
        await db.flush()
        db.add_all(
            [
                _transition(tenant, r1.id, None, "queued", T0),
                _transition(tenant, r1.id, "queued", "running", _sec(1)),
                _transition(tenant, r1.id, "running", "done", _sec(10)),
                _transition(tenant, r2.id, None, "queued", T0),
                _transition(tenant, r2.id, "queued", "running", _sec(1)),
                _transition(tenant, r2.id, "running", "done", _sec(20)),
            ]
        )
        await db.flush()
        result = await compute_kpis(db, tenant_id=tenant, agent_id=agent)
    # (10s + 20s) / 2, measured from each run's created_at (T0).
    assert result.total_duration_ms == 15_000


async def test_total_duration_uses_the_first_terminal_transition(
    app_session: AppSessionFactory,
) -> None:
    """`_total_duration_ms` groups by `run_id` and takes MIN(at), so if a run
    ever had two terminal transition rows only the first would count. This
    codebase's state machine (`states.py`'s `_ALLOWED`, where every terminal
    state is absorbing) plus `RunRepository.transition` being the sole writer
    of `RunStateTransition` rows means that situation cannot arise through
    normal operation -- so the two rows here are built directly at the
    ORM/fixture level, bypassing `.transition()` entirely, rather than via a
    `running -> failed -> queued -> running -> done` sequence that
    `assert_transition` would actually reject. This proves the SQL's own
    behavior (defense in depth) independent of whether that history is
    reachable today."""
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    async with app_session(tenant) as db:
        run = _run(tenant, agent)
        db.add(run)
        await db.flush()
        db.add_all(
            [
                _transition(tenant, run.id, "running", "failed", _sec(10)),
                _transition(tenant, run.id, "running", "done", _sec(30)),
            ]
        )
        await db.flush()
        result = await compute_kpis(db, tenant_id=tenant, agent_id=agent)
    assert result.run_count == 1
    assert result.total_duration_ms == 10_000


async def test_total_duration_is_none_when_no_run_has_finished(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    async with app_session(tenant) as db:
        run = _run(tenant, agent)
        db.add(run)
        await db.flush()
        db.add(_transition(tenant, run.id, "queued", "running", _sec(1)))
        await db.flush()
        result = await compute_kpis(db, tenant_id=tenant, agent_id=agent)
    assert result.run_count == 1
    assert result.total_duration_ms is None


async def test_execution_duration_excludes_approval_wait_time(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    async with app_session(tenant) as db:
        run = _run(tenant, agent)
        db.add(run)
        await db.flush()
        db.add_all(
            [
                _transition(tenant, run.id, None, "queued", _sec(-5)),
                _transition(tenant, run.id, "queued", "running", _sec(0)),
                _transition(tenant, run.id, "running", "waiting_for_approval", _sec(10)),
                _transition(tenant, run.id, "waiting_for_approval", "running", _sec(40)),
                _transition(tenant, run.id, "running", "done", _sec(50)),
            ]
        )
        await db.flush()
        result = await compute_kpis(db, tenant_id=tenant, agent_id=agent)
    # (10 - 0) + (50 - 40) = 20s of running; the 30s in approval wait must not count.
    assert result.execution_duration_ms == 20_000


async def test_approval_wait_is_none_when_nothing_is_decided(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(
            m.ApprovalRequest(
                tenant_id=tenant,
                agent_id=agent,
                department_id=uuid.uuid4(),
                action_type="tool_call",
                status="pending",
                created_at=T0,
                updated_at=T0,
            )
        )
        await db.flush()
        result = await compute_kpis(db, tenant_id=tenant, agent_id=agent)
    assert result.approval_wait_ms is None


async def test_approval_wait_averages_decided_requests(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add_all(
            [
                m.ApprovalRequest(
                    tenant_id=tenant,
                    agent_id=agent,
                    department_id=uuid.uuid4(),
                    action_type="tool_call",
                    status="approved",
                    created_at=T0,
                    updated_at=T0,
                    decided_at=_sec(4),
                ),
                m.ApprovalRequest(
                    tenant_id=tenant,
                    agent_id=agent,
                    department_id=uuid.uuid4(),
                    action_type="tool_call",
                    status="rejected",
                    created_at=T0,
                    updated_at=T0,
                    decided_at=_sec(6),
                ),
                # Still pending: must not drag the average toward zero.
                m.ApprovalRequest(
                    tenant_id=tenant,
                    agent_id=agent,
                    department_id=uuid.uuid4(),
                    action_type="tool_call",
                    status="pending",
                    created_at=T0,
                    updated_at=T0,
                ),
            ]
        )
        await db.flush()
        result = await compute_kpis(db, tenant_id=tenant, agent_id=agent)
    assert result.approval_wait_ms == 5_000


async def test_response_time_computed_from_consecutive_chat_messages(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    async with app_session(tenant) as db:
        session = m.ChatSession(
            id=uuid.uuid4(),
            tenant_id=tenant,
            agent_id=agent,
            member_id=uuid.uuid4(),
            title="t",
        )
        db.add(session)
        await db.flush()
        db.add_all(
            [
                m.ChatMessage(
                    tenant_id=tenant,
                    session_id=session.id,
                    role="user",
                    content="hi",
                    created_at=T0,
                    updated_at=T0,
                ),
                m.ChatMessage(
                    tenant_id=tenant,
                    session_id=session.id,
                    role="assistant",
                    content="hello",
                    created_at=_sec(5),
                    updated_at=_sec(5),
                ),
                # A continued turn: assistant following assistant is not a
                # response to anything, so it must not enter the average.
                m.ChatMessage(
                    tenant_id=tenant,
                    session_id=session.id,
                    role="assistant",
                    content="...and one more thing",
                    created_at=_sec(60),
                    updated_at=_sec(60),
                ),
            ]
        )
        await db.flush()
        scoped = await compute_kpis(db, tenant_id=tenant, agent_id=agent)
        tenant_wide = await compute_kpis(db, tenant_id=tenant)
    assert scoped.response_time_ms == 5_000
    assert tenant_wide.response_time_ms == 5_000


async def test_response_time_is_none_for_a_run_with_no_chat_messages(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(_run(tenant, agent))
        await db.flush()
        result = await compute_kpis(db, tenant_id=tenant, agent_id=agent)
    assert result.run_count == 1
    assert result.response_time_ms is None


async def test_avg_tool_call_duration_ignores_entries_with_no_durationms(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(
            _run(
                tenant,
                agent,
                context={
                    "toolCalls": [
                        {"tool": "x", "durationMs": 100},
                        {"tool": "y"},
                    ]
                },
            )
        )
        # A run with no toolCalls key at all must not break the query.
        db.add(_run(tenant, agent, context={"output": "done"}))
        await db.flush()
        result = await compute_kpis(db, tenant_id=tenant, agent_id=agent)
    assert result.avg_tool_call_duration_ms == 100


async def test_avg_tool_call_duration_ignores_a_non_numeric_durationms(
    app_session: AppSessionFactory,
) -> None:
    """A `durationMs` that is not a JSON number -- a string from legacy or
    hand-edited data, or an explicit `null` -- must not make `CAST(... AS
    NUMERIC)` raise and 500 the whole tenant's aggregate. It should simply be
    excluded, the same as an entry with no `durationMs` key at all."""
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(
            _run(
                tenant,
                agent,
                context={
                    "toolCalls": [
                        {"tool": "x", "durationMs": 100},
                        {"tool": "y"},
                        {"tool": "z", "durationMs": "fast"},
                        {"tool": "w", "durationMs": None},
                    ]
                },
            )
        )
        await db.flush()
        result = await compute_kpis(db, tenant_id=tenant, agent_id=agent)
    # Only the genuinely-numeric entry (100) contributes to the average.
    assert result.avg_tool_call_duration_ms == 100


async def test_avg_tool_call_duration_is_none_without_any_timed_call(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(_run(tenant, agent, context={"toolCalls": [{"tool": "y"}]}))
        await db.flush()
        result = await compute_kpis(db, tenant_id=tenant, agent_id=agent)
    assert result.avg_tool_call_duration_ms is None


async def test_department_scope_joins_through_agent_for_runs_and_chat(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(id=uuid.uuid4(), tenant_id=tenant, name="Sales")
        other = m.Department(id=uuid.uuid4(), tenant_id=tenant, name="Support")
        db.add_all([dept, other])
        await db.flush()
        a1 = m.Agent(id=uuid.uuid4(), tenant_id=tenant, department_id=dept.id, name="A1")
        a2 = m.Agent(id=uuid.uuid4(), tenant_id=tenant, department_id=dept.id, name="A2")
        a3 = m.Agent(id=uuid.uuid4(), tenant_id=tenant, department_id=other.id, name="A3")
        db.add_all([a1, a2, a3])
        await db.flush()
        db.add_all([_run(tenant, a1.id), _run(tenant, a2.id), _run(tenant, a3.id)])
        session = m.ChatSession(
            id=uuid.uuid4(),
            tenant_id=tenant,
            agent_id=a1.id,
            member_id=uuid.uuid4(),
            title="t",
        )
        db.add(session)
        await db.flush()
        db.add_all(
            [
                m.ChatMessage(
                    tenant_id=tenant,
                    session_id=session.id,
                    role="user",
                    content="hi",
                    created_at=T0,
                    updated_at=T0,
                ),
                m.ChatMessage(
                    tenant_id=tenant,
                    session_id=session.id,
                    role="assistant",
                    content="hello",
                    created_at=_sec(3),
                    updated_at=_sec(3),
                ),
            ]
        )
        await db.flush()
        result = await compute_kpis(db, tenant_id=tenant, department_id=dept.id)
        elsewhere = await compute_kpis(db, tenant_id=tenant, department_id=other.id)
    assert result.run_count == 2
    assert result.response_time_ms == 3_000
    assert elsewhere.run_count == 1
    assert elsewhere.response_time_ms is None


async def test_department_scope_does_not_join_through_agent_for_approvals(
    app_session: AppSessionFactory,
) -> None:
    """An agent that MOVED departments keeps its old approvals in the old
    department's numbers -- `ApprovalRequest.department_id` is authoritative."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        raised_in = m.Department(id=uuid.uuid4(), tenant_id=tenant, name="Old")
        moved_to = m.Department(id=uuid.uuid4(), tenant_id=tenant, name="New")
        db.add_all([raised_in, moved_to])
        await db.flush()
        agent = m.Agent(id=uuid.uuid4(), tenant_id=tenant, department_id=moved_to.id, name="Mover")
        db.add(agent)
        await db.flush()
        db.add(
            m.ApprovalRequest(
                tenant_id=tenant,
                agent_id=agent.id,
                department_id=raised_in.id,
                action_type="tool_call",
                status="approved",
                created_at=T0,
                updated_at=T0,
                decided_at=_sec(8),
            )
        )
        await db.flush()
        old = await compute_kpis(db, tenant_id=tenant, department_id=raised_in.id)
        new = await compute_kpis(db, tenant_id=tenant, department_id=moved_to.id)
    assert old.approval_wait_ms == 8_000
    assert new.approval_wait_ms is None


async def test_date_range_narrows_every_run_scoped_metric(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    async with app_session(tenant) as db:
        old = _run(
            tenant,
            agent,
            created_at=T0 - dt.timedelta(days=30),
            context={"toolCalls": [{"tool": "x", "durationMs": 900}]},
        )
        recent = _run(tenant, agent, context={"toolCalls": [{"tool": "x", "durationMs": 100}]})
        db.add_all([old, recent])
        await db.flush()
        result = await compute_kpis(
            db,
            tenant_id=tenant,
            agent_id=agent,
            date_from=T0 - dt.timedelta(days=1),
            date_to=T0 + dt.timedelta(days=1),
        )
    assert result.run_count == 1
    assert result.avg_tool_call_duration_ms == 100


async def test_status_narrows_to_one_run_state(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add_all([_run(tenant, agent, state="done"), _run(tenant, agent, state="failed")])
        await db.flush()
        done_only = await compute_kpis(db, tenant_id=tenant, agent_id=agent, status="done")
        failed_only = await compute_kpis(db, tenant_id=tenant, agent_id=agent, status="failed")
        unfiltered = await compute_kpis(db, tenant_id=tenant, agent_id=agent)
    assert done_only.run_count == 1
    assert failed_only.run_count == 1
    assert unfiltered.run_count == 2


async def test_another_tenants_rows_never_leak_into_a_scope(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    other_tenant = uuid.uuid4()
    async with app_session(other_tenant) as db:
        db.add(_run(other_tenant, agent))
        await db.flush()
    async with app_session(tenant) as db:
        db.add(_run(tenant, agent))
        await db.flush()
        result = await compute_kpis(db, tenant_id=tenant, agent_id=agent)
    assert result.run_count == 1


async def test_compute_kpis_takes_no_group_by_parameter() -> None:
    """Grouping is the CALLER's job (`api/v1/kpis.py` loops and assembles the
    rows). The signature used to accept a `group_by` and discard it with
    `_ = group_by`, so anything but the endpoint got ungrouped numbers back
    with no error at all -- a silent no-op on a public function. Pinned here so
    re-adding the parameter means implementing it, not re-adding the trap."""
    import inspect

    assert "group_by" not in inspect.signature(compute_kpis).parameters
