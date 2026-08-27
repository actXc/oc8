from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.metering import check_budget, resume_budget_scope, set_budget
from oc8.metering.usage import record_usage
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def _dept_agent(s: AsyncSession, tenant: uuid.UUID, dept: uuid.UUID) -> m.Agent:
    agent = m.Agent(tenant_id=tenant, department_id=dept, name="Dev", status="paused")
    agent.pause_reason = "budget"
    s.add(agent)
    await s.flush()
    return agent


async def test_override_suppresses_hard_exceeded(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    dept = uuid.uuid4()
    async with app_session(tenant) as s:
        await set_budget(
            s, tenant_id=tenant, department_id=dept,
            soft_limit_tokens=None, hard_limit_tokens=100,
        )
        await record_usage(
            s, tenant_id=tenant, department_id=dept, agent_id=uuid.uuid4(),
            request_id=uuid.uuid4(), provider="p", model="m", tokens_in=90, tokens_out=90,
        )
        check = await check_budget(s, tenant_id=tenant, department_id=dept)
        assert check.hard_exceeded is True

        budget = await s.execute(
            select(m.Budget).where(m.Budget.department_id == dept)
        )
        b = budget.scalar_one()
        b.override_until = dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=1)
        await s.flush()
        check2 = await check_budget(s, tenant_id=tenant, department_id=dept)
        assert check2.hard_exceeded is False  # override active

        b.override_until = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=1)
        await s.flush()
        check3 = await check_budget(s, tenant_id=tenant, department_id=dept)
        assert check3.hard_exceeded is True  # override expired


async def test_resume_clears_only_budget_pauses(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    dept = uuid.uuid4()
    async with app_session(tenant) as s:
        budget_agent = await _dept_agent(s, tenant, dept)
        sup_agent = m.Agent(tenant_id=tenant, department_id=dept, name="Sup", status="paused")
        sup_agent.pause_reason = "supervision"
        s.add(sup_agent)
        await s.flush()

        resumed = await resume_budget_scope(s, tenant_id=tenant, department_id=dept)
        assert resumed == 1
        assert budget_agent.status == "idle" and budget_agent.pause_reason is None
        assert sup_agent.status == "paused" and sup_agent.pause_reason == "supervision"


async def test_set_budget_raise_resumes_scope(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    dept = uuid.uuid4()
    async with app_session(tenant) as s:
        await set_budget(
            s, tenant_id=tenant, department_id=dept,
            soft_limit_tokens=None, hard_limit_tokens=100,
        )
        await record_usage(
            s, tenant_id=tenant, department_id=dept, agent_id=uuid.uuid4(),
            request_id=uuid.uuid4(), provider="p", model="m", tokens_in=90, tokens_out=90,
        )
        agent = await _dept_agent(s, tenant, dept)
        # still hard-exceeded (180 >= 100): raising the limit above usage resumes.
        await set_budget(
            s, tenant_id=tenant, department_id=dept,
            soft_limit_tokens=None, hard_limit_tokens=1000,
        )
        assert agent.status == "idle" and agent.pause_reason is None
