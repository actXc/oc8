from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from oc8.metering import check_budget, set_budget, trigger_budget_hard_stop
from oc8.metering.usage import record_usage
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role="org_admin")
    return {"Authorization": f"Bearer {token}"}


async def _breach(app_session: AppSessionFactory, tenant: uuid.UUID, dept: uuid.UUID) -> uuid.UUID:
    async with app_session(tenant) as s:
        await set_budget(
            s, tenant_id=tenant, department_id=dept, soft_limit_tokens=None, hard_limit_tokens=100
        )
        await record_usage(
            s, tenant_id=tenant, department_id=dept, agent_id=uuid.uuid4(),
            request_id=uuid.uuid4(), provider="p", model="m", tokens_in=200, tokens_out=0,
        )
        agent = m.Agent(tenant_id=tenant, department_id=dept, name="A", status="running")
        s.add(agent)
        await s.flush()
        incident = await trigger_budget_hard_stop(s, tenant_id=tenant, breaching_agent=agent)
        assert incident is not None
        return incident.id


async def test_approve_sets_override_and_resumes(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    dept = uuid.uuid4()
    incident_id = await _breach(app_session, tenant, dept)

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post(
                f"/api/v1/approvals/{incident_id}/decision",
                headers=_headers(tenant),
                json={"decision": "approve"},
            )
            assert r.status_code == 200
            assert r.json()["status"] == "approved"

    async with app_session(tenant) as s:
        # override set, scope resumed, check now passes
        check = await check_budget(s, tenant_id=tenant, department_id=dept)
        assert check.hard_exceeded is False
        agents = (await s.execute(
            select(m.Agent).where(m.Agent.department_id == dept)
        )).scalars().all()
        assert all(a.status != "paused" for a in agents)


async def test_reject_leaves_scope_paused(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    dept = uuid.uuid4()
    incident_id = await _breach(app_session, tenant, dept)

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post(
                f"/api/v1/approvals/{incident_id}/decision",
                headers=_headers(tenant),
                json={"decision": "reject"},
            )
            assert r.status_code == 200
            assert r.json()["status"] == "rejected"

    async with app_session(tenant) as s:
        check = await check_budget(s, tenant_id=tenant, department_id=dept)
        assert check.hard_exceeded is True  # still over, no override
        agents = (await s.execute(
            select(m.Agent).where(m.Agent.department_id == dept)
        )).scalars().all()
        assert any(a.status == "paused" and a.pause_reason == "budget" for a in agents)
