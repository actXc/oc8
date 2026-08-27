"""`PUT /agents/{id}/narrowing` requires `connection_id` when enabling a
login-backed tool: a `narrowing.tools` key that names an `McpConnection` row
with `credential_id` set (a login, per Task 3's `POST /mcp/logins`) must not
be enabled without pointing at that connection -- no silent fallback to
"whichever login happens to be there"."""

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


async def _seed_agent_with_frame_tool(
    app_session: AppSessionFactory, tenant: uuid.UUID, tool_key: str
) -> uuid.UUID:
    async with app_session(tenant) as db:
        dept = m.Department(
            tenant_id=tenant, name="Sales", frame={"tools": {tool_key: _FRAME_TOOL_POLICY}}
        )
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Nora")
        db.add(agent)
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
        return agent.id


async def test_enabling_a_login_tool_without_a_connection_id_is_rejected(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent_id = await _seed_agent_with_frame_tool(app_session, tenant, "Odoo (User 1)")
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.put(
                f"/api/v1/agents/{agent_id}/narrowing",
                json={"narrowing": {"tools": {"Odoo (User 1)": {"enabled": True}}}},
                headers=_headers(tenant),
            )
            assert r.status_code == 422, r.text
            assert "login" in r.text.lower() or "connection" in r.text.lower()


async def test_enabling_a_login_tool_with_a_connection_id_succeeds(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent_id = await _seed_agent_with_frame_tool(app_session, tenant, "Odoo (User 1)")
    async with app_session(tenant) as db:
        conn = (await db.execute(select(m.McpConnection))).scalar_one()
        conn_id = str(conn.id)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.put(
                f"/api/v1/agents/{agent_id}/narrowing",
                json={
                    "narrowing": {
                        "tools": {"Odoo (User 1)": {"enabled": True, "connection_id": conn_id}}
                    }
                },
                headers=_headers(tenant),
            )
            assert r.status_code == 200, r.text


async def test_disabling_a_login_tool_skips_the_check(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent_id = await _seed_agent_with_frame_tool(app_session, tenant, "Odoo (User 1)")
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.put(
                f"/api/v1/agents/{agent_id}/narrowing",
                json={"narrowing": {"tools": {"Odoo (User 1)": {"enabled": False}}}},
                headers=_headers(tenant),
            )
            assert r.status_code == 200, r.text


async def test_get_agent_surfaces_the_pinned_connection_id_in_effective_tools(
    app_session: AppSessionFactory,
) -> None:
    """A `connection_id` pin set via `PUT .../narrowing` must round-trip back
    out of `GET /agents/{id}`'s `effectiveTools[key].connectionId` -- the
    frontend's login picker (Task 8, `agents.$id.tsx`) seeds its initial
    selection from exactly this field."""
    tenant = uuid.uuid4()
    agent_id = await _seed_agent_with_frame_tool(app_session, tenant, "Odoo (User 1)")
    async with app_session(tenant) as db:
        conn = (await db.execute(select(m.McpConnection))).scalar_one()
        conn_id = str(conn.id)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            put_r = await c.put(
                f"/api/v1/agents/{agent_id}/narrowing",
                json={
                    "narrowing": {
                        "tools": {"Odoo (User 1)": {"enabled": True, "connection_id": conn_id}}
                    }
                },
                headers=_headers(tenant),
            )
            assert put_r.status_code == 200, put_r.text
            get_r = await c.get(f"/api/v1/agents/{agent_id}", headers=_headers(tenant))
            assert get_r.status_code == 200, get_r.text
            effective_tools = get_r.json()["effectiveTools"]
            assert effective_tools["Odoo (User 1)"]["connectionId"] == conn_id


async def test_get_agent_surfaces_approval_actions_and_only_in_effective_tools(
    app_session: AppSessionFactory,
) -> None:
    """`ToolPolicyDTO` used to omit `approval_actions`/`only` entirely, even
    though `ToolPolicy.to_json()` (and the raw frame JSON) always carry them --
    a Guardrails editor reading `effectiveTools` back could never tell which
    specific actions were already gated, and would silently reset that list to
    empty on the very first save."""
    tenant = uuid.uuid4()
    agent_id = await _seed_agent_with_frame_tool(app_session, tenant, "Odoo (User 1)")
    async with app_session(tenant) as db:
        conn = (await db.execute(select(m.McpConnection))).scalar_one()
        conn_id = str(conn.id)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            put_r = await c.put(
                f"/api/v1/agents/{agent_id}/narrowing",
                json={
                    "narrowing": {
                        "tools": {
                            "Odoo (User 1)": {
                                "enabled": True,
                                "connection_id": conn_id,
                                "approval_actions": ["delete_record"],
                            }
                        }
                    }
                },
                headers=_headers(tenant),
            )
            assert put_r.status_code == 200, put_r.text
            get_r = await c.get(f"/api/v1/agents/{agent_id}", headers=_headers(tenant))
            assert get_r.status_code == 200, get_r.text
            effective = get_r.json()["effectiveTools"]["Odoo (User 1)"]
            assert effective["approvalActions"] == ["delete_record"]


async def test_enabling_a_tool_with_no_login_connection_is_unaffected(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(
            tenant_id=tenant,
            name="Sales",
            frame={"tools": {"knowledge_search": _FRAME_TOOL_POLICY}},
        )
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Nora")
        db.add(agent)
        await db.flush()
        agent_id = agent.id
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.put(
                f"/api/v1/agents/{agent_id}/narrowing",
                json={"narrowing": {"tools": {"knowledge_search": {"enabled": True}}}},
                headers=_headers(tenant),
            )
            assert r.status_code == 200, r.text
