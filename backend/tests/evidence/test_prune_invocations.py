"""Dropping the idempotency cache once nothing can replay against it.

`tool_invocation` is not evidence, and that is the whole argument for deleting
it outright rather than archiving it: a call's ARGUMENTS live in the audit
ledger for the full retention period, and its RESULT lives in the run's
transcript, which is archived and hash-chained. What this table adds is the
ability to answer a REPEAT of the same call within the same task without acting
twice -- an operational cache whose useful life ends when the task does.

So the only question that matters here is when it has ended, and the answer is
not `task.state`: 65 of this deployment's tasks sit in `in_progress` with a
FAILED run behind them, because the failure path never closed them. Keying on
that column would leak those rows for ever. The predicate is about runs.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from sqlalchemy import select, update

from oc8 import models as m
from oc8.config import get_settings
from oc8.evidence.sweep import sweep_evidence
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

LONG_AGO = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)


class NoEvidenceRuntime:
    """Keeps the evidence half of the sweep out of the way of these tests."""


@pytest.fixture
def sweep_on(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "evidence_sweep_enabled", True, raising=False)
    monkeypatch.setattr(settings, "evidence_archive_after_minutes", 60, raising=False)
    monkeypatch.setattr(settings, "evidence_retention_days", 0, raising=False)


async def _agent(db: Any, tenant: uuid.UUID) -> m.Agent:
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
        status="idle",
        narrowing={},
        definition={},
        presentation={},
    )
    db.add(agent)
    await db.flush()
    return agent


async def _call(
    db: Any, tenant: uuid.UUID, task_id: uuid.UUID, *, tool: str = "update_record"
) -> uuid.UUID:
    row = m.ToolInvocation(
        tenant_id=tenant,
        task_id=task_id,
        tool=tool,
        args_hash=uuid.uuid4().hex,
        result="{'id': 7}",
    )
    db.add(row)
    await db.flush()
    return row.id


async def _run_for(
    db: Any, tenant: uuid.UUID, agent: m.Agent, task_id: uuid.UUID, state: str
) -> m.AgentRun:
    run = m.AgentRun(tenant_id=tenant, agent_id=agent.id, task_id=task_id, state=state, context={})
    db.add(run)
    await db.flush()
    return run


async def _age_all(db: Any, tenant: uuid.UUID, when: dt.datetime) -> None:
    """One statement, one commit -- a commit unbinds the transaction-local
    tenant GUC and everything after it would silently match nothing."""
    await db.execute(
        update(m.ToolInvocation).where(m.ToolInvocation.tenant_id == tenant).values(created_at=when)
    )
    await db.execute(
        update(m.AgentRun).where(m.AgentRun.tenant_id == tenant).values(updated_at=when)
    )
    await db.commit()


async def _remaining(db: Any, tenant: uuid.UUID) -> set[uuid.UUID]:
    return set(
        (await db.execute(select(m.ToolInvocation.id).where(m.ToolInvocation.tenant_id == tenant)))
        .scalars()
        .all()
    )


async def test_a_finished_task_loses_its_cache(
    app_session: AppSessionFactory, sweep_on: None
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = await _agent(db, tenant)
        task_id = uuid.uuid4()
        call_id = await _call(db, tenant, task_id)
        await _run_for(db, tenant, agent, task_id, "done")
        await _age_all(db, tenant, LONG_AGO)

    report = await sweep_evidence(resolve=lambda **_: NoEvidenceRuntime())

    assert report.invocations_pruned >= 1
    async with app_session(tenant) as db:
        assert call_id not in await _remaining(db, tenant)


async def test_a_task_whose_run_is_still_going_keeps_it(
    app_session: AppSessionFactory, sweep_on: None
) -> None:
    """The cache is what stops a restarted leg acting twice. Taking it while a
    run is alive is exactly the case it exists for."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = await _agent(db, tenant)
        task_id = uuid.uuid4()
        call_id = await _call(db, tenant, task_id)
        await _run_for(db, tenant, agent, task_id, "running")
        await _age_all(db, tenant, LONG_AGO)

    await sweep_evidence(resolve=lambda **_: NoEvidenceRuntime())

    async with app_session(tenant) as db:
        assert call_id in await _remaining(db, tenant)


async def test_a_parked_run_keeps_it(app_session: AppSessionFactory, sweep_on: None) -> None:
    """A run held for approval resumes into the SAME task and reproduces the
    approved call. Without its cache row it would make the call for real."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = await _agent(db, tenant)
        task_id = uuid.uuid4()
        call_id = await _call(db, tenant, task_id)
        await _run_for(db, tenant, agent, task_id, "waiting_for_approval")
        await _age_all(db, tenant, LONG_AGO)

    await sweep_evidence(resolve=lambda **_: NoEvidenceRuntime())

    async with app_session(tenant) as db:
        assert call_id in await _remaining(db, tenant)


async def test_one_live_run_protects_a_tasks_whole_cache(
    app_session: AppSessionFactory, sweep_on: None
) -> None:
    """A task can have several runs; ONE of them still alive is enough."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = await _agent(db, tenant)
        task_id = uuid.uuid4()
        call_id = await _call(db, tenant, task_id)
        await _run_for(db, tenant, agent, task_id, "failed")
        await _run_for(db, tenant, agent, task_id, "queued")
        await _age_all(db, tenant, LONG_AGO)

    await sweep_evidence(resolve=lambda **_: NoEvidenceRuntime())

    async with app_session(tenant) as db:
        assert call_id in await _remaining(db, tenant)


async def test_a_stale_task_row_does_not_hold_the_cache_open(
    app_session: AppSessionFactory, sweep_on: None
) -> None:
    """The case that chose the predicate.

    65 tasks on the live system sit in `in_progress` behind a FAILED run: the
    failure path never closed them. Keying the prune on `task.state` would keep
    their cache rows for ever, so the rule asks about RUNS instead.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = await _agent(db, tenant)
        task = m.Task(
            tenant_id=tenant,
            department_id=agent.department_id,
            assigned_agent_id=agent.id,
            title="offen geblieben",
            state="in_progress",
        )
        db.add(task)
        await db.flush()
        call_id = await _call(db, tenant, task.id)
        await _run_for(db, tenant, agent, task.id, "failed")
        await _age_all(db, tenant, LONG_AGO)

    await sweep_evidence(resolve=lambda **_: NoEvidenceRuntime())

    async with app_session(tenant) as db:
        assert call_id not in await _remaining(db, tenant)


async def test_an_invocation_with_no_run_at_all_is_left_alone(
    app_session: AppSessionFactory, sweep_on: None
) -> None:
    """Unknown is not the same as done -- the container reaper's own rule. A
    row whose run this sweep cannot see is never deleted on a guess."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _agent(db, tenant)
        call_id = await _call(db, tenant, uuid.uuid4())
        await _age_all(db, tenant, LONG_AGO)

    await sweep_evidence(resolve=lambda **_: NoEvidenceRuntime())

    async with app_session(tenant) as db:
        assert call_id in await _remaining(db, tenant)


async def test_a_fresh_row_is_inside_the_grace_window(
    app_session: AppSessionFactory, sweep_on: None
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = await _agent(db, tenant)
        task_id = uuid.uuid4()
        call_id = await _call(db, tenant, task_id)
        await _run_for(db, tenant, agent, task_id, "done")
        await db.commit()

    await sweep_evidence(resolve=lambda **_: NoEvidenceRuntime())

    async with app_session(tenant) as db:
        assert call_id in await _remaining(db, tenant)


async def test_a_disabled_sweep_prunes_nothing(
    app_session: AppSessionFactory, sweep_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "evidence_sweep_enabled", False, raising=False)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = await _agent(db, tenant)
        task_id = uuid.uuid4()
        call_id = await _call(db, tenant, task_id)
        await _run_for(db, tenant, agent, task_id, "done")
        await _age_all(db, tenant, LONG_AGO)

    report = await sweep_evidence(resolve=lambda **_: NoEvidenceRuntime())

    assert report.invocations_pruned == 0
    async with app_session(tenant) as db:
        assert call_id in await _remaining(db, tenant)


async def test_another_tenants_cache_is_untouched(
    app_session: AppSessionFactory, sweep_on: None
) -> None:
    """The sweep visits every tenant, so each pass must only reach its own."""
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    async with app_session(theirs) as db:
        agent = await _agent(db, theirs)
        task_id = uuid.uuid4()
        their_call = await _call(db, theirs, task_id)
        # Their run is still going, so their row must survive a sweep that
        # deletes mine.
        await _run_for(db, theirs, agent, task_id, "running")
        await _age_all(db, theirs, LONG_AGO)
    async with app_session(mine) as db:
        agent = await _agent(db, mine)
        task_id = uuid.uuid4()
        my_call = await _call(db, mine, task_id)
        await _run_for(db, mine, agent, task_id, "done")
        await _age_all(db, mine, LONG_AGO)

    await sweep_evidence(resolve=lambda **_: NoEvidenceRuntime())

    async with app_session(mine) as db:
        assert my_call not in await _remaining(db, mine)
    async with app_session(theirs) as db:
        assert their_call in await _remaining(db, theirs)


async def test_the_outward_delivery_memo_follows_the_same_rule(
    app_session: AppSessionFactory, sweep_on: None
) -> None:
    """`remember_delivery` writes into this table too, under an `outward:` name.
    It is the same kind of fact -- "this already happened for this task" -- and
    it expires with the task for the same reason."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = await _agent(db, tenant)
        task_id = uuid.uuid4()
        memo = await _call(db, tenant, task_id, tool="outward:kunde@example.com")
        await _run_for(db, tenant, agent, task_id, "done")
        await _age_all(db, tenant, LONG_AGO)

    await sweep_evidence(resolve=lambda **_: NoEvidenceRuntime())

    async with app_session(tenant) as db:
        assert memo not in await _remaining(db, tenant)
