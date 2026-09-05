"""Tests for agent narrowing override tracking.

`_enforce_narrowing_logins` (agents_write.py) is the ONLY writer of
`Agent.narrowing_overridden_keys` — the column the new "N of M agents deviate"
feature will read. A tool key is only "overridden" if an operator's save
actually changed its value from what was stored before, not merely because
it appears in the payload.
"""

from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _token(tenant: uuid.UUID) -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")


async def test_resaving_an_unchanged_tool_does_not_mark_it_as_overridden(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        department = m.Department(
            tenant_id=tenant,
            name=f"D-{uuid.uuid4().hex}",
            frame={"tools": {"github": {"enabled": True, "read": True, "modify": False}}},
        )
        db.add(department)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant,
            department_id=department.id,
            name="Probe",
            narrowing={"tools": {"github": {"enabled": True, "read": True, "modify": False}}},
        )
        db.add(agent)
        await db.flush()
        agent_id = agent.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            # Resave the narrowing with the exact same value as what's already stored.
            r = await client.put(
                f"/api/v1/agents/{agent_id}/narrowing",
                json={
                    "narrowing": {
                        "tools": {"github": {"enabled": True, "read": True, "modify": False}}
                    }
                },
                headers=headers,
            )
            assert r.status_code == 200, r.text

    async with app_session(tenant) as db:
        refreshed = await db.get(m.Agent, agent_id)
        assert refreshed.narrowing_overridden_keys == [], (
            "resending a value identical to what was already stored must not mark it as a "
            "deliberate override"
        )


async def test_resaving_a_tool_with_a_changed_value_does_mark_it_as_overridden(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        department = m.Department(
            tenant_id=tenant,
            name=f"D-{uuid.uuid4().hex}",
            frame={"tools": {"github": {"enabled": True, "read": True, "modify": True}}},
        )
        db.add(department)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=department.id, name="Probe")
        db.add(agent)
        await db.flush()
        agent_id = agent.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.put(
                f"/api/v1/agents/{agent_id}/narrowing",
                json={
                    "narrowing": {
                        "tools": {"github": {"enabled": True, "read": True, "modify": False}}
                    }
                },
                headers=headers,
            )
            assert r.status_code == 200, r.text

    async with app_session(tenant) as db:
        refreshed = await db.get(m.Agent, agent_id)
        assert refreshed.narrowing_overridden_keys == ["github"]
