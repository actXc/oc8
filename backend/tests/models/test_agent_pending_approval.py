from __future__ import annotations

import uuid

import pytest

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_agent_status_allows_pending_approval(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        agent = m.Agent(
            tenant_id=tenant, department_id=uuid.uuid4(), name="New",
            status="pending_approval",
        )
        s.add(agent)
        await s.flush()  # the CHECK must accept the new value
    async with app_session(tenant) as s:
        a = await s.get(m.Agent, agent.id)
        assert a is not None and a.status == "pending_approval"
