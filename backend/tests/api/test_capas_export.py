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


def _headers(tenant: uuid.UUID, role: str = "org_admin") -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role=role)
    return {"Authorization": f"Bearer {token}"}


async def _seed_department(app_session: AppSessionFactory, tenant: uuid.UUID) -> uuid.UUID:
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
        db.add(dept)
        await db.flush()
        lead = m.Agent(
            tenant_id=tenant,
            department_id=dept.id,
            name="Nora",
            is_team_lead=True,
            status="stopped",
        )
        db.add(lead)
        await db.flush()
        dept.team_lead_agent_id = lead.id
        return dept.id


async def test_export_requires_plugin_manage_permission(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    dept_id = await _seed_department(app_session, tenant)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/capas/export",
                json={
                    "items": [
                        {
                            "kind": "department",
                            "id": str(dept_id),
                            "name": "vertrieb",
                            "version": "1.0.0",
                            "summary": "",
                        }
                    ],
                    "dry_run": True,
                },
                # "member" holds the empty permission set (BUILTIN_ROLE_PERMISSIONS
                # in authz/permissions.py) -- confirmed against that table, since
                # plugin:manage is NEVER_DELEGATABLE and cannot come from a
                # tenant-defined role either.
                headers=_headers(tenant, role="member"),
            )
            assert r.status_code == 403, r.text


async def test_export_dry_run_returns_manifest_preview(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    dept_id = await _seed_department(app_session, tenant)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/capas/export",
                json={
                    "items": [
                        {
                            "kind": "department",
                            "id": str(dept_id),
                            "name": "vertrieb",
                            "version": "1.0.0",
                            "summary": "Vertriebsteam",
                        }
                    ],
                    "dry_run": True,
                },
                headers=_headers(tenant),
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["items"][0]["folder_name"] == "vertrieb"
            assert "[plugin]" in body["items"][0]["manifest_toml"]
            assert body["errors"] == []


async def test_export_real_returns_zip(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    dept_id = await _seed_department(app_session, tenant)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/capas/export",
                json={
                    "items": [
                        {
                            "kind": "department",
                            "id": str(dept_id),
                            "name": "vertrieb",
                            "version": "1.0.0",
                            "summary": "",
                        }
                    ],
                    "dry_run": False,
                },
                headers=_headers(tenant),
            )
            assert r.status_code == 200, r.text
            assert r.headers["content-type"] == "application/zip"
            assert "attachment" in r.headers["content-disposition"]


async def test_export_unknown_item_id_returns_422(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant):
        pass  # tenant exists in RLS terms even with zero rows
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/capas/export",
                json={
                    "items": [
                        {
                            "kind": "department",
                            "id": str(uuid.uuid4()),
                            "name": "ghost",
                            "version": "1.0.0",
                            "summary": "",
                        }
                    ],
                    "dry_run": True,
                },
                headers=_headers(tenant),
            )
            assert r.status_code == 422, r.text
