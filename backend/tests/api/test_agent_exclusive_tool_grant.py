"""`PUT /agents/{id}/narrowing` now accepts a tool key the department frame
never granted -- an agent-exclusive grant (pdp.py's `narrowing_within_frame`/
`effective_tool_policies`), invisible to sibling agents in the same
department unless the department later adds that tool to its own frame."""

from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role="org_admin")
    return {"Authorization": f"Bearer {token}"}


async def _seed_department_with_two_agents(
    app_session: AppSessionFactory, tenant: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    async with app_session(tenant) as db:
        dept = m.Department(
            tenant_id=tenant,
            name="Helpdesk",
            frame={
                "tools": {
                    "odoo": {"enabled": True, "read": True, "modify": False, "modify": False}
                }
            },
        )
        db.add(dept)
        await db.flush()
        lennart = m.Agent(tenant_id=tenant, department_id=dept.id, name="Lennart")
        tim = m.Agent(tenant_id=tenant, department_id=dept.id, name="Tim")
        db.add_all([lennart, tim])
        await db.flush()
        return lennart.id, tim.id


async def test_enabling_a_tool_absent_from_the_frame_succeeds(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    lennart_id, _ = await _seed_department_with_two_agents(app_session, tenant)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.put(
                f"/api/v1/agents/{lennart_id}/narrowing",
                json={
                    "narrowing": {
                        "tools": {
                            "odoo": {"enabled": True, "read": True},
                            "salesforce": {"enabled": True, "read": True, "modify": False},
                        }
                    }
                },
                headers=_headers(tenant),
            )
            assert r.status_code == 200, r.text


async def test_agent_exclusive_tool_shows_up_in_effective_tools(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    lennart_id, _ = await _seed_department_with_two_agents(app_session, tenant)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            put_r = await c.put(
                f"/api/v1/agents/{lennart_id}/narrowing",
                json={
                    "narrowing": {"tools": {"salesforce": {"enabled": True, "read": True}}}
                },
                headers=_headers(tenant),
            )
            assert put_r.status_code == 200, put_r.text
            get_r = await c.get(f"/api/v1/agents/{lennart_id}", headers=_headers(tenant))
            assert get_r.status_code == 200, get_r.text
            body = get_r.json()
            assert body["effectiveTools"]["salesforce"]["enabled"] is True
            # Not the department's declared frame -- this is what marks it
            # agent-exclusive on the frontend (departmentFrameTools vs
            # effectiveTools diff).
            assert "salesforce" not in body["departmentFrameTools"]


async def test_a_sibling_agent_in_the_same_department_does_not_inherit_the_exclusive_grant(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    lennart_id, tim_id = await _seed_department_with_two_agents(app_session, tenant)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            put_r = await c.put(
                f"/api/v1/agents/{lennart_id}/narrowing",
                json={
                    "narrowing": {"tools": {"salesforce": {"enabled": True, "read": True}}}
                },
                headers=_headers(tenant),
            )
            assert put_r.status_code == 200, put_r.text
            tim_r = await c.get(f"/api/v1/agents/{tim_id}", headers=_headers(tenant))
            assert tim_r.status_code == 200, tim_r.text
            assert "salesforce" not in tim_r.json()["effectiveTools"]
