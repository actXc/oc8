from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.metering import set_budget, trigger_budget_hard_stop
from oc8.metering.usage import record_usage
from oc8.runtime.executor import execute_run
from oc8.runtime.queue import RunMessage
from oc8.runtime.repository import RunRepository
from oc8.runtime.states import RunState
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def _seed_scope(
    s: AsyncSession, tenant: uuid.UUID, dept: uuid.UUID
) -> tuple[m.Agent, m.Agent, uuid.UUID]:
    """Two idle agents + one already running, in one department, plus a queued
    run for the idle one. Returns (breaching_agent, idle_agent, queued_run_id)."""
    breaching = m.Agent(tenant_id=tenant, department_id=dept, name="A", status="running")
    idle = m.Agent(tenant_id=tenant, department_id=dept, name="B", status="idle")
    s.add_all([breaching, idle])
    await s.flush()
    run = await RunRepository(s).create(tenant_id=tenant, agent_id=idle.id, context={"task": "x"})
    return breaching, idle, run.id


async def test_department_breach_freezes_scope_and_raises_one_incident(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    dept = uuid.uuid4()
    async with app_session(tenant) as s:
        await set_budget(
            s, tenant_id=tenant, department_id=dept, soft_limit_tokens=None, hard_limit_tokens=100
        )
        await record_usage(
            s,
            tenant_id=tenant,
            department_id=dept,
            agent_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            provider="p",
            model="m",
            tokens_in=200,
            tokens_out=0,
        )
        breaching, idle, queued_run_id = await _seed_scope(s, tenant, dept)

        incident = await trigger_budget_hard_stop(s, tenant_id=tenant, breaching_agent=breaching)
        assert incident is not None and incident.action_type == "budget_incident"
        assert incident.status == "pending"
        # scope frozen: breaching + idle both paused for budget
        assert breaching.status == "paused" and breaching.pause_reason == "budget"
        assert idle.status == "paused" and idle.pause_reason == "budget"
        # queued run got a run_cancellation row
        rc = (
            await s.execute(
                select(m.RunCancellation).where(m.RunCancellation.run_id == queued_run_id)
            )
        ).scalar_one_or_none()
        assert rc is not None and rc.cancellation_kind == "budget_hard_stop"

        # idempotency: a second breaching agent in the scope adds NO second incident
        second = m.Agent(tenant_id=tenant, department_id=dept, name="C", status="running")
        s.add(second)
        await s.flush()
        again = await trigger_budget_hard_stop(s, tenant_id=tenant, breaching_agent=second)
        assert again is None
        assert second.status == "paused" and second.pause_reason == "budget"
        incidents = (
            (
                await s.execute(
                    select(m.ApprovalRequest).where(
                        m.ApprovalRequest.action_type == "budget_incident"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(incidents) == 1

    # the queued run drains to interrupted via §7.2's before-start check
    await execute_run(
        RunMessage(
            run_id=str(queued_run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False
        ),
        runtime=_never_runs(),
    )
    async with app_session(tenant) as s:
        run = await s.get(m.AgentRun, queued_run_id)
        assert run is not None and run.state == RunState.INTERRUPTED.value
        # The frozen idle agent must STAY paused after its queued run drains: the
        # executor must NOT un-freeze a budget-paused agent when cancelling its
        # queued run (A2 review Critical -- an un-freeze would also orphan
        # pause_reason and poison the scope's idempotency check).
        idle_after = await s.get(m.Agent, idle.id)
        assert idle_after is not None
        assert idle_after.status == "paused" and idle_after.pause_reason == "budget"


def _never_runs() -> Any:
    class _R:
        async def execute(self, db: Any, **kw: Any) -> Any:
            raise AssertionError("a budget-cancelled queued run must not execute")

    return _R()


async def test_tenant_wide_breach_freezes_whole_tenant(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    dept_a, dept_b = uuid.uuid4(), uuid.uuid4()
    async with app_session(tenant) as s:
        await set_budget(
            s, tenant_id=tenant, department_id=None, soft_limit_tokens=None, hard_limit_tokens=100
        )
        await record_usage(
            s,
            tenant_id=tenant,
            department_id=dept_a,
            agent_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            provider="p",
            model="m",
            tokens_in=200,
            tokens_out=0,
        )
        breaching = m.Agent(tenant_id=tenant, department_id=dept_a, name="A", status="running")
        other_dept = m.Agent(tenant_id=tenant, department_id=dept_b, name="Z", status="idle")
        s.add_all([breaching, other_dept])
        await s.flush()

        await trigger_budget_hard_stop(s, tenant_id=tenant, breaching_agent=breaching)
        # tenant-wide breach freezes an idle agent in a DIFFERENT department too
        assert other_dept.status == "paused" and other_dept.pause_reason == "budget"


async def test_department_then_tenant_breach_raises_both_incidents(
    app_session: AppSessionFactory,
) -> None:
    """A department incident must NOT suppress a later tenant-wide incident.
    Idempotency is keyed on the pending budget_incident's scope, not on agent
    pause-state (a department freeze pauses agents that a naive agent-based check
    would mistake for a tenant freeze -> the tenant incident would go unraised)."""
    tenant = uuid.uuid4()
    dept = uuid.uuid4()
    async with app_session(tenant) as s:
        # Dept over at 100; tenant-wide over only once usage passes 300.
        await set_budget(
            s, tenant_id=tenant, department_id=dept, soft_limit_tokens=None, hard_limit_tokens=100
        )
        await set_budget(
            s, tenant_id=tenant, department_id=None, soft_limit_tokens=None, hard_limit_tokens=300
        )
        await record_usage(
            s, tenant_id=tenant, department_id=dept, agent_id=uuid.uuid4(),
            request_id=uuid.uuid4(), provider="p", model="m", tokens_in=200, tokens_out=0,
        )
        a1 = m.Agent(tenant_id=tenant, department_id=dept, name="A1", status="running")
        s.add(a1)
        await s.flush()
        # 1st breach: dept over (200>=100), tenant-wide not (200<300) -> dept scope.
        first = await trigger_budget_hard_stop(s, tenant_id=tenant, breaching_agent=a1)
        assert first is not None and first.payload["scope"] == "department"

        # push tenant-wide over the edge (400>=300)
        await record_usage(
            s, tenant_id=tenant, department_id=dept, agent_id=uuid.uuid4(),
            request_id=uuid.uuid4(), provider="p", model="m", tokens_in=200, tokens_out=0,
        )
        a2 = m.Agent(tenant_id=tenant, department_id=dept, name="A2", status="running")
        s.add(a2)
        await s.flush()
        # 2nd breach: tenant-wide now over -> tenant scope; must raise a SECOND,
        # tenant-scoped incident (not suppressed by the department freeze).
        second = await trigger_budget_hard_stop(s, tenant_id=tenant, breaching_agent=a2)
        assert second is not None and second.payload["scope"] == "tenant"

        incidents = (
            (await s.execute(
                select(m.ApprovalRequest).where(
                    m.ApprovalRequest.action_type == "budget_incident"
                )
            )).scalars().all()
        )
        scopes = sorted(i.payload["scope"] for i in incidents)
        assert scopes == ["department", "tenant"]
