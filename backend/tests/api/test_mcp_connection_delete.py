from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID, role: str = "org_admin") -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role=role)
    return {"Authorization": f"Bearer {token}"}


async def _make_conn(db: AsyncSession, tenant: uuid.UUID, name: str = "c") -> uuid.UUID:
    conn = m.McpConnection(
        tenant_id=tenant,
        name=name,
        server_url="",
        transport="stdio",
        scopes=[],
        config={"command": "true", "args": []},
        connected=False,
    )
    db.add(conn)
    await db.flush()
    return conn.id


async def test_delete_removes_the_connection(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        conn_id = await _make_conn(db, tenant)
        await db.commit()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.delete(f"/api/v1/mcp/connections/{conn_id}", headers=_headers(tenant))
            assert r.status_code == 204, r.text

            listed = await c.get("/api/v1/mcp/connections", headers=_headers(tenant))
            assert all(row["id"] != str(conn_id) for row in listed.json())


async def test_delete_unknown_id_returns_404(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant):
        pass  # tenant exists in RLS terms even with zero rows
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.delete(
                f"/api/v1/mcp/connections/{uuid.uuid4()}", headers=_headers(tenant)
            )
            assert r.status_code == 404, r.text


async def test_delete_never_reaches_another_tenants_connection(
    app_session: AppSessionFactory,
) -> None:
    owner_tenant = uuid.uuid4()
    async with app_session(owner_tenant) as db:
        conn_id = await _make_conn(db, owner_tenant)
        await db.commit()

    attacker_tenant = uuid.uuid4()
    async with app_session(attacker_tenant):
        pass
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.delete(
                f"/api/v1/mcp/connections/{conn_id}", headers=_headers(attacker_tenant)
            )
            assert r.status_code == 404, r.text

            # Still there, from the owner's own side.
            listed = await c.get("/api/v1/mcp/connections", headers=_headers(owner_tenant))
            assert any(row["id"] == str(conn_id) for row in listed.json())


async def test_delete_requires_integration_manage_permission(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        conn_id = await _make_conn(db, tenant)
        await db.commit()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.delete(
                f"/api/v1/mcp/connections/{conn_id}", headers=_headers(tenant, role="member")
            )
            assert r.status_code == 403, r.text
