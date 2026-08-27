"""POST /departments -- the "start empty" door beside
POST /plugins/{id}/instantiate-department (which ships a template's
pre-built agents). Same tenant-wide `department:manage` gate as
PATCH /departments/{id} and PUT /departments/{id}/tools -- no seat can ever
hold it."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID, role: str) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="u", role=role)
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def test_org_admin_can_create_a_blank_department(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.post(
            "/api/v1/departments",
            json={"name": "Engineering", "goal": "Keep the site up", "icon": "engineering"},
            headers=h,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Engineering"
        assert body["goal"] == "Keep the site up"
        assert body["icon"] == "engineering"
        assert body["promptCachingEnabled"] is True

    async with app_session(tenant) as db:
        row = (
            await db.execute(select(m.Department).where(m.Department.id == uuid.UUID(body["id"])))
        ).scalar_one()
        assert row.name == "Engineering"
        assert row.tenant_id == tenant


async def test_created_department_actually_persists_and_is_listed(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        await http.post("/api/v1/departments", json={"name": "Sales"}, headers=h)
        await http.post("/api/v1/departments", json={"name": "Engineering"}, headers=h)

        resp = await http.get("/api/v1/departments", headers=h)
        assert resp.status_code == 200, resp.text
        # `GET /departments` returns `Page[DepartmentDTO]` (Task 7 of the
        # Design System Consistency plan), not a bare list.
        names = {d["name"] for d in resp.json()["items"]}
        assert names == {"Sales", "Engineering"}


async def test_created_department_gets_a_working_memory_frame(
    app_session: AppSessionFactory,
) -> None:
    """A department created with an empty `memory` policy would DENY
    department-tier memory writes (memory/policy.py) -- silently leaving
    every agent hired into it unable to remember anything. Same default the
    tenant's own first, auto-provisioned department gets."""
    tenant = uuid.uuid4()
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.post("/api/v1/departments", json={"name": "Sales"}, headers=h)
        dept_id = uuid.UUID(resp.json()["id"])

    async with app_session(tenant) as db:
        row = await db.get(m.Department, dept_id)
        assert row is not None
        assert row.frame["memory"] == {"department": ["read", "write"], "company": ["read"]}


async def test_defaults_apply_when_goal_and_icon_are_omitted(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.post("/api/v1/departments", json={"name": "Support"}, headers=h)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["goal"] == ""
        assert body["icon"] == "building"


async def test_member_role_is_refused(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with _http() as http:
        h = _headers(tenant, "member")
        resp = await http.post("/api/v1/departments", json={"name": "Engineering"}, headers=h)
        assert resp.status_code == 403, resp.text


async def test_empty_name_is_rejected(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.post("/api/v1/departments", json={"name": ""}, headers=h)
        assert resp.status_code == 422, resp.text


async def test_missing_name_is_rejected(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.post("/api/v1/departments", json={}, headers=h)
        assert resp.status_code == 422, resp.text
