"""`POST /agents` must apply the same login-guard `PUT /agents/{id}/
narrowing` already does: a `narrowing.tools` key that names an
`McpConnection` row with `credential_id` set (a login, per Task 3's
`POST /mcp/logins`) must not be enabled without pointing at that
connection -- no silent fallback. Before this fix, `create_agent` ran
`narrowing_within_frame` but skipped the login check and never populated
`narrowing_overridden_keys`, so an agent could be hired with a login tool
"enabled" and no connection, silently unresolvable at runtime (agent tool
login selection design, Hire-dialog credential-picker follow-up)."""

from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role="org_admin")
    return {"Authorization": f"Bearer {token}"}


_FRAME_TOOL_POLICY = {"enabled": True, "read": True, "write": False, "send": False}


async def _seed_department_with_login(
    app_session: AppSessionFactory, tenant: uuid.UUID, tool_key: str
) -> tuple[uuid.UUID, uuid.UUID]:
    async with app_session(tenant) as db:
        dept = m.Department(
            tenant_id=tenant, name="Sales", frame={"tools": {tool_key: _FRAME_TOOL_POLICY}}
        )
        db.add(dept)
        await db.flush()
        cred = m.Credential(tenant_id=tenant, name="Odoo User 1", credential_type="odoo_login")
        db.add(cred)
        await db.flush()
        conn = m.McpConnection(
            tenant_id=tenant,
            name=tool_key,
            server_url="",
            transport="stdio",
            credential_id=cred.id,
        )
        db.add(conn)
        await db.flush()
        return dept.id, conn.id


async def test_hiring_with_a_login_tool_enabled_and_no_connection_id_is_rejected(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    dept_id, _conn_id = await _seed_department_with_login(app_session, tenant, "Odoo (User 1)")
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/agents",
                json={
                    "name": "Nora",
                    "departmentId": str(dept_id),
                    "narrowing": {"tools": {"Odoo (User 1)": {"enabled": True}}},
                },
                headers=_headers(tenant),
            )
            assert r.status_code == 422, r.text
            assert "login" in r.text.lower() or "connection" in r.text.lower()


async def test_hiring_with_a_login_tool_and_connection_id_succeeds_and_records_override(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    dept_id, conn_id = await _seed_department_with_login(app_session, tenant, "Odoo (User 1)")
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/agents",
                json={
                    "name": "Nora",
                    "departmentId": str(dept_id),
                    "narrowing": {
                        "tools": {
                            "Odoo (User 1)": {"enabled": True, "connection_id": str(conn_id)}
                        }
                    },
                },
                headers=_headers(tenant),
            )
            assert r.status_code == 201, r.text
            agent_id = r.json()["id"]

    async with app_session(tenant) as db:
        agent = (
            await db.execute(select(m.Agent).where(m.Agent.id == uuid.UUID(agent_id)))
        ).scalar_one()
        assert agent.narrowing_overridden_keys == ["Odoo (User 1)"]


async def test_hiring_with_no_narrowing_at_all_is_unaffected(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Sales", frame={})
        db.add(dept)
        await db.flush()
        dept_id = dept.id
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/agents",
                json={"name": "Nora", "departmentId": str(dept_id)},
                headers=_headers(tenant),
            )
            assert r.status_code == 201, r.text
