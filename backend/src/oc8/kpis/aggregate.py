"""Live KPI aggregation. One function, one scope, no rollup tables -- see
docs/superpowers/specs/2026-08-28-agent-kpis-design.md §2 for why.

Six independent sub-queries compose the result rather than one giant join,
matching this codebase's existing preference for composed small units: each is
independently testable against a narrow fixture, and a slow or wrong sub-query
is isolated rather than buried in a 200-line SELECT.

Every sub-query filters on `tenant_id` EXPLICITLY even though RLS already binds
the session to one tenant: these are aggregates, and an aggregate that silently
returns the wrong tenant's average because a caller passed a `tenant_id` the
session is not bound to would be indistinguishable from a correct answer.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any, Literal, TypedDict, Unpack

from sqlalchemy import (
    ColumnElement,
    Numeric,
    Select,
    case,
    cast,
    column,
    extract,
    func,
    literal,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from oc8.approvals import repo as approvals_repo
from oc8.models import (
    Agent,
    AgentRun,
    ChatMessage,
    ChatSession,
    RunStateTransition,
)

#: The grouping vocabulary, named here so the endpoint layer and this module
#: agree on the spelling. `compute_kpis` deliberately does NOT take it as a
#: parameter: grouping is performed by the caller (see its docstring).
GroupBy = Literal["agent", "department", "day", "week", "month"]

#: Which `to_state` values end a run. A run that reaches one of these has a
#: total duration; one that has not is still in flight and contributes nothing.
_TERMINAL_STATES = ("done", "failed", "interrupted")


@dataclass(frozen=True)
class KPIResult:
    """One scope's numbers. Every duration is milliseconds, and `None` means
    "nothing in scope to measure" -- deliberately not 0, which would read as
    "measured, and it was instant"."""

    run_count: int
    total_duration_ms: int | None
    execution_duration_ms: int | None
    approval_wait_ms: int | None
    response_time_ms: int | None
    avg_tool_call_duration_ms: int | None
    #: For a grouped result, the RAW UUID string of the agent/department (or the
    #: ISO date of the bucket). Never a display name: resolving one would mean
    #: this module knew about presentation, and the caller -- Task 4's endpoint,
    #: the frontend's agent list -- already holds the name it would look up.
    group_key: str | None = None


class _Scope(TypedDict):
    """The six arguments every sub-query is scoped by. A TypedDict rather than
    six repeated parameters so `compute_kpis` can hand the identical scope to
    all six sub-queries and mypy still checks each call."""

    tenant_id: uuid.UUID
    agent_id: uuid.UUID | None
    department_id: uuid.UUID | None
    date_from: dt.datetime | None
    date_to: dt.datetime | None
    #: An `AgentRun.state` value (e.g. `"done"`, `"failed"`), or `None` for no
    #: narrowing. Only meaningful to the four sub-queries that filter through
    #: `_base_run_filter` -- `_approval_wait_ms`/`_response_time_ms` scope
    #: `ApprovalRequest`/`ChatMessage` directly and have no `AgentRun` row to
    #: read a state off in the first place, the same reason those two already
    #: build their own conditions instead of calling `_base_run_filter`.
    status: str | None


def _agents_in_department(
    tenant_id: uuid.UUID, department_id: uuid.UUID
) -> Select[tuple[uuid.UUID]]:
    """Agent ids currently in a department. Soft-deleted agents are deliberately
    INCLUDED: their runs really did happen for this department, and dropping
    them would make a historical figure change when somebody deletes an agent."""
    return select(Agent.id).where(
        Agent.tenant_id == tenant_id, Agent.department_id == department_id
    )


def _base_run_filter(**scope: Unpack[_Scope]) -> list[ColumnElement[bool]]:
    """Shared WHERE-clause builder every AgentRun-scoped sub-query uses, so
    agent/department/date-range/status scoping cannot drift between them."""
    tenant_id = scope["tenant_id"]
    conditions: list[ColumnElement[bool]] = [AgentRun.tenant_id == tenant_id]
    if scope["agent_id"] is not None:
        conditions.append(AgentRun.agent_id == scope["agent_id"])
    if scope["department_id"] is not None:
        conditions.append(
            AgentRun.agent_id.in_(_agents_in_department(tenant_id, scope["department_id"]))
        )
    if scope["date_from"] is not None:
        conditions.append(AgentRun.created_at >= scope["date_from"])
    if scope["date_to"] is not None:
        conditions.append(AgentRun.created_at <= scope["date_to"])
    if scope["status"] is not None:
        conditions.append(AgentRun.state == scope["status"])
    return conditions


def _ms(later: Any, earlier: Any) -> ColumnElement[Any]:
    """`EXTRACT(EPOCH FROM (later - earlier)) * 1000` -- the one place this
    module turns a timestamp difference into milliseconds. `Any` because the two
    operands are sometimes mapped columns and sometimes subquery columns, whose
    static types differ while the SQL is identical."""
    return extract("epoch", later - earlier) * 1000


def _as_int(value: Any) -> int | None:
    """AVG returns NUMERIC (a Decimal), and NULL when nothing matched. Round
    rather than truncate: a 4999.6 ms average is 5000 ms, not 4999."""
    if value is None:
        return None
    return round(float(value))


async def _run_count(db: AsyncSession, **scope: Unpack[_Scope]) -> int:
    stmt = select(func.count(AgentRun.id)).where(*_base_run_filter(**scope))
    return int((await db.execute(stmt)).scalar_one())


async def _total_duration_ms(db: AsyncSession, **scope: Unpack[_Scope]) -> int | None:
    """Average wall-clock time from a run's `created_at` to the moment it first
    reached a terminal state.

    MIN(at) GROUPED BY run, not a plain join to every terminal transition: a
    plain join would count a run twice in the average if it ever had two
    terminal transition rows. This codebase's state machine (`states.py`'s
    `_ALLOWED`) makes every terminal state absorbing, and `RunRepository.transition`
    is the sole writer of `RunStateTransition` rows, so a run acquiring two
    terminal rows through normal operation is unreachable today. The GROUP BY
    is defense in depth against a duplicated or backfilled row rather than a
    guard against a live code path -- cheap (the existing index serves it) and
    still the semantically correct choice ("how long did this take" means the
    FIRST terminal moment) should that invariant ever change.

    AVG, not SUM: "how long does a task take" is a per-run figure. A SUM would
    simply grow with the number of runs in the window and answer nothing.
    """
    conditions = _base_run_filter(**scope)
    terminal_at = (
        select(
            RunStateTransition.run_id.label("run_id"),
            func.min(RunStateTransition.at).label("at"),
        )
        .where(
            RunStateTransition.tenant_id == scope["tenant_id"],
            RunStateTransition.to_state.in_(_TERMINAL_STATES),
        )
        .group_by(RunStateTransition.run_id)
        .subquery()
    )
    stmt = (
        select(func.avg(_ms(terminal_at.c.at, AgentRun.created_at)))
        .select_from(AgentRun)
        .join(terminal_at, terminal_at.c.run_id == AgentRun.id)
        .where(*conditions)
    )
    return _as_int((await db.execute(stmt)).scalar_one())


async def _execution_duration_ms(db: AsyncSession, **scope: Unpack[_Scope]) -> int | None:
    """Average time a run spent actually RUNNING -- approval and input waits
    excluded.

    Shape: `LEAD(at) OVER (PARTITION BY run_id ORDER BY at)` over the tenant's
    transition log gives every transition the moment the NEXT one happened. A
    row whose `to_state` is 'running' therefore describes exactly one running
    interval, `next_at - at`. Summing those per run and averaging across runs is
    the whole computation; the run may enter and leave 'running' any number of
    times and the arithmetic does not change.

    The trailing transition of every run has a NULL `next_at` (nothing follows
    it), which is filtered out rather than left to SUM's NULL-skipping so the
    intent is on the page. A run still in 'running' right now contributes only
    its already-closed intervals -- the open one has no end to measure.

    The scope is pushed INTO the windowed subquery (`run_id IN (<scoped runs>)`)
    rather than left to the outer join alone. Without it the LEAD ran over every
    transition row the tenant has ever written before a single one was thrown
    away, so an agent-, department-, or date-scoped call did the same work as a
    tenant-wide one -- and the endpoint's grouped mode repeats that whole scan
    once per group. This is safe precisely because `run_id` is the window's own
    PARTITION BY key: removing whole partitions cannot change the LEAD computed
    within any partition that remains, and the outer join already discarded
    those rows anyway.
    """
    conditions = _base_run_filter(**scope)
    scoped_runs = select(AgentRun.id).where(*conditions)
    next_at = (
        func.lead(RunStateTransition.at)
        .over(partition_by=RunStateTransition.run_id, order_by=RunStateTransition.at)
        .label("next_at")
    )
    windowed = (
        select(
            RunStateTransition.run_id.label("run_id"),
            RunStateTransition.to_state.label("to_state"),
            RunStateTransition.at.label("at"),
            next_at,
        )
        .where(
            RunStateTransition.tenant_id == scope["tenant_id"],
            RunStateTransition.run_id.in_(scoped_runs),
        )
        .subquery()
    )
    per_run = (
        select(
            windowed.c.run_id,
            func.sum(_ms(windowed.c.next_at, windowed.c.at)).label("running_ms"),
        )
        .where(windowed.c.to_state == "running", windowed.c.next_at.isnot(None))
        .group_by(windowed.c.run_id)
        .subquery()
    )
    stmt = (
        select(func.avg(per_run.c.running_ms))
        .select_from(AgentRun)
        .join(per_run, per_run.c.run_id == AgentRun.id)
        .where(*conditions)
    )
    return _as_int((await db.execute(stmt)).scalar_one())


async def _approval_wait_ms(db: AsyncSession, **scope: Unpack[_Scope]) -> int | None:
    """Average time a decided approval sat waiting for a human.

    The actual query lives in `approvals.repo.avg_wait_ms`, not here: that
    module is the one `tests/approvals/test_reads_go_through_the_scoped_repository.py`
    allows to load `ApprovalRequest` at all, and its docstring covers both why
    this is safe with no actor in sight (a single AVG(), no row ever reaches a
    caller) and why the department scope stays direct rather than joining
    through `Agent.department_id` (spec §1.3).
    """
    return await approvals_repo.avg_wait_ms(
        db,
        tenant_id=scope["tenant_id"],
        agent_id=scope["agent_id"],
        department_id=scope["department_id"],
        date_from=scope["date_from"],
        date_to=scope["date_to"],
    )


async def _response_time_ms(db: AsyncSession, **scope: Unpack[_Scope]) -> int | None:
    """Average gap between an operator's chat message and the agent's reply.

    Same window-function shape as `_execution_duration_ms`, mirrored: `LAG` over
    the session's messages gives each row its predecessor's timestamp AND role,
    and a row is a measurable response exactly when it is an 'assistant' message
    whose predecessor was a 'user' message. Two consecutive assistant messages
    (a continued turn) are therefore not counted twice.

    The agent/department narrowing goes through `ChatSession.agent_id`, and is
    applied only when a narrower scope was actually asked for -- a tenant-wide
    call needs no join at all. That narrowing is pushed INTO the windowed
    subquery (`session_id IN (<scoped sessions>)`), for the same reason
    `_execution_duration_ms` pushes its run scope in and with the same safety
    argument: `session_id` is the window's PARTITION BY key, so dropping whole
    sessions before the LAG runs cannot change the value LAG computes inside any
    session that survives.

    The DATE range deliberately stays OUTSIDE the window, unlike the session
    scope. It is not a partition key: an operator message sent just before
    `date_from`, whose reply lands just after it, is the predecessor LAG has to
    see in order for that reply's response time to exist at all. Filtering it
    out first would silently turn measurable replies into NULLs at every window
    boundary -- which is exactly what makes "push the filter down" a
    per-predicate decision here rather than a blanket one.
    """
    tenant_id = scope["tenant_id"]
    message_conditions: list[ColumnElement[bool]] = [ChatMessage.tenant_id == tenant_id]
    if scope["agent_id"] is not None or scope["department_id"] is not None:
        scoped_sessions = select(ChatSession.id).where(ChatSession.tenant_id == tenant_id)
        if scope["agent_id"] is not None:
            scoped_sessions = scoped_sessions.where(ChatSession.agent_id == scope["agent_id"])
        if scope["department_id"] is not None:
            scoped_sessions = scoped_sessions.where(
                ChatSession.agent_id.in_(_agents_in_department(tenant_id, scope["department_id"]))
            )
        message_conditions.append(ChatMessage.session_id.in_(scoped_sessions))
    prev_at = (
        func.lag(ChatMessage.created_at)
        .over(partition_by=ChatMessage.session_id, order_by=ChatMessage.created_at)
        .label("prev_at")
    )
    prev_role = (
        func.lag(ChatMessage.role)
        .over(partition_by=ChatMessage.session_id, order_by=ChatMessage.created_at)
        .label("prev_role")
    )
    windowed = (
        select(
            ChatMessage.session_id.label("session_id"),
            ChatMessage.role.label("role"),
            ChatMessage.created_at.label("created_at"),
            prev_at,
            prev_role,
        )
        .where(*message_conditions)
        .subquery()
    )
    conditions: list[ColumnElement[bool]] = [
        windowed.c.role == "assistant",
        windowed.c.prev_role == "user",
    ]
    if scope["date_from"] is not None:
        conditions.append(windowed.c.created_at >= scope["date_from"])
    if scope["date_to"] is not None:
        conditions.append(windowed.c.created_at <= scope["date_to"])
    stmt = select(func.avg(_ms(windowed.c.created_at, windowed.c.prev_at))).where(*conditions)
    return _as_int((await db.execute(stmt)).scalar_one())


async def _avg_tool_call_duration_ms(db: AsyncSession, **scope: Unpack[_Scope]) -> int | None:
    """Average `durationMs` across every timed tool call in scope.

    `jsonb_array_elements` expands `context->'toolCalls'` into one row per entry
    -- an implicitly LATERAL set-returning function in the FROM list, which is
    what `table_valued(..., joins_implicitly=True)` renders:

        SELECT avg((e.value ->> 'durationMs')::numeric)
        FROM agent_run,
             jsonb_array_elements(CASE WHEN jsonb_typeof(context->'toolCalls') = 'array'
                                       THEN context->'toolCalls' ELSE '[]'::jsonb END) AS e(value)
        WHERE <scope> AND e.value ->> 'durationMs' IS NOT NULL

    The CASE, not a COALESCE, is load-bearing: `jsonb_array_elements` RAISES on
    anything that is not a JSON array, and it is evaluated in the FROM clause --
    before WHERE -- so no filter can protect it. A run whose context has no
    `toolCalls` key, or (from an older or hand-edited row) a non-array there,
    must contribute nothing rather than fail the whole query. `jsonb_typeof` of
    a missing key is NULL, which takes the ELSE branch, so both cases collapse
    into the same empty array.

    Entries with no `durationMs`, or a `durationMs` that is not itself a JSON
    number, are dropped: Task 2 writes the key only for calls that actually
    executed -- a blocked or approval-suspended call has no duration, and
    counting it as anything would be an invention -- but legacy or hand-edited
    rows can hold a `durationMs` of the wrong shape (a string, `null`, an
    object). `CAST(... AS NUMERIC)` raises on any of those, and unlike
    `jsonb_array_elements` above it is NOT protected by evaluation order: it
    runs as part of the SELECT list over rows the WHERE clause already let
    through, so the guard has to live in WHERE itself. `func.jsonb_typeof(...)
    == "number"` excludes a missing key, `null`, a string, and an object in one
    predicate -- symmetric with the array guard -- so CAST only ever sees a
    genuine JSON number.

    The average is per CALL, not per run: a run with ten tool calls weighs ten
    times as much as a run with one, which is what "average tool call duration"
    means.

    One rendering detail: SQLAlchemy 2.0 compiles `AgentRun.context["toolCalls"]`
    to the jsonb SUBSCRIPT form `context['toolCalls']` rather than `context ->
    'toolCalls'`. They mean the same thing, but subscripting jsonb needs
    PostgreSQL 14+ -- fine here (the repo runs 15 everywhere, including the test
    container), and noted only so nobody backports this to an older server.
    """
    conditions = _base_run_filter(**scope)
    tool_calls = AgentRun.context["toolCalls"]
    as_array = case(
        (func.jsonb_typeof(tool_calls) == "array", tool_calls),
        else_=cast(literal("[]"), JSONB),
    )
    entries = func.jsonb_array_elements(as_array).table_valued(
        column("value", JSONB), joins_implicitly=True
    )
    duration = entries.c.value["durationMs"]
    stmt = (
        select(func.avg(cast(duration.astext, Numeric)))
        .select_from(AgentRun)
        .where(*conditions, func.jsonb_typeof(duration) == "number")
    )
    return _as_int((await db.execute(stmt)).scalar_one())


async def compute_kpis(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    date_from: dt.datetime | None = None,
    date_to: dt.datetime | None = None,
    status: str | None = None,
) -> KPIResult:
    """Every KPI for ONE scope. Pass no `agent_id`/`department_id` for the
    tenant-wide figures.

    GROUPING IS THE CALLER'S RESPONSIBILITY, and there is deliberately no
    `group_by` parameter here to suggest otherwise. `api/v1/kpis.py` calls this
    once per group value -- once per agent, once per department, once per date
    bucket -- and assembles the list itself. That keeps this function genuinely
    one thing (a single scope's numbers) and keeps "which groups exist?" in the
    layer that already has to answer it to build a response. It also removes
    the trap the earlier signature carried: a `group_by` argument that was
    accepted and then silently discarded, so a caller outside the endpoint
    would have got ungrouped numbers back with no error. Revisit only if the
    per-group round trip proves too slow at real scale (spec §7);
    `kpis.aggregate.GroupBy` names the shared vocabulary in the meantime.

    `status` narrows to one `AgentRun.state` value (spec §3/§4.3), the same
    optional-narrowing shape `agent_id`/`department_id` already have. It only
    reaches the four sub-queries scoped through `_base_run_filter` -- see
    `_Scope.status`'s own docstring for why `_approval_wait_ms`/
    `_response_time_ms` are unaffected by it.
    """
    scope: _Scope = {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "department_id": department_id,
        "date_from": date_from,
        "date_to": date_to,
        "status": status,
    }
    return KPIResult(
        run_count=await _run_count(db, **scope),
        total_duration_ms=await _total_duration_ms(db, **scope),
        execution_duration_ms=await _execution_duration_ms(db, **scope),
        approval_wait_ms=await _approval_wait_ms(db, **scope),
        response_time_ms=await _response_time_ms(db, **scope),
        avg_tool_call_duration_ms=await _avg_tool_call_duration_ms(db, **scope),
    )
