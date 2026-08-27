from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from oc8 import models as m
from oc8.constants import ACME_TENANT_ID
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_agent_run_persists_and_is_tenant_scoped(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    agent_id = uuid.uuid4()
    async with app_session(tenant) as s:
        run = m.AgentRun(tenant_id=tenant, agent_id=agent_id, context={"task": "hi"})
        s.add(run)
        await s.flush()
        run_id = run.id
        assert run.state == "queued"

    async with app_session(tenant) as s:
        loaded = await s.get(m.AgentRun, run_id)
        assert loaded is not None
        assert loaded.context == {"task": "hi"}

    # The agent_run table must have RLS enabled.
    async with app_session(tenant) as s:
        enabled = (
            await s.execute(text("SELECT relrowsecurity FROM pg_class WHERE relname = 'agent_run'"))
        ).scalar_one()
    assert enabled is True
