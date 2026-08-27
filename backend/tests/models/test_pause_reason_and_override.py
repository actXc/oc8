from __future__ import annotations

import uuid

import pytest

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_agent_pause_fields_and_budget_override_default_null(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="Dev")
        s.add(agent)
        budget = m.Budget(
            tenant_id=tenant, department_id=None, soft_limit_tokens=None, hard_limit_tokens=100
        )
        s.add(budget)
        await s.flush()
        assert agent.pause_reason is None
        assert agent.paused_at is None
        assert budget.override_until is None

    async with app_session(tenant) as s:
        a = await s.get(m.Agent, agent.id)
        assert a is not None
        a.pause_reason = "budget"
        await s.flush()
    async with app_session(tenant) as s:
        a = await s.get(m.Agent, agent.id)
        assert a is not None and a.pause_reason == "budget"
