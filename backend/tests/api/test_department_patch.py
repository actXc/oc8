"""PATCH /departments/{id} -- the one new backend surface the gamified
onboarding wizard's step 1 needs (design:
gamified-onboarding-wizard-design.md). Tenant-wide `department:manage` only,
same gate as PUT /departments/{id}/tools -- no seat can ever hold it."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

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


async def _seed_department(app_session: AppSessionFactory) -> tuple[uuid.UUID, uuid.UUID]:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(
            tenant_id=tenant,
            name="Sales",
            goal="",
            frame={},
            presentation={"icon": "building", "slug": "sales"},
        )
        db.add(dept)
        await db.flush()
        return tenant, dept.id


async def test_org_admin_can_rename_and_retheme_department(
    app_session: AppSessionFactory,
) -> None:
    tenant, dept_id = await _seed_department(app_session)
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.patch(
            f"/api/v1/departments/{dept_id}",
            json={"name": "Vertrieb", "goal": "Close deals", "icon": "sales"},
            headers=h,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "Vertrieb"
        assert body["goal"] == "Close deals"
        assert body["icon"] == "sales"


async def test_partial_update_leaves_other_fields_unchanged(
    app_session: AppSessionFactory,
) -> None:
    tenant, dept_id = await _seed_department(app_session)
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.patch(
            f"/api/v1/departments/{dept_id}", json={"name": "Vertrieb"}, headers=h
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "Vertrieb"
        assert body["icon"] == "building"  # untouched


async def test_member_role_is_refused(app_session: AppSessionFactory) -> None:
    tenant, dept_id = await _seed_department(app_session)
    async with _http() as http:
        h = _headers(tenant, "member")
        resp = await http.patch(
            f"/api/v1/departments/{dept_id}", json={"name": "Vertrieb"}, headers=h
        )
        assert resp.status_code == 403, resp.text


async def test_bogus_department_id_404s(app_session: AppSessionFactory) -> None:
    tenant, _ = await _seed_department(app_session)
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.patch(
            f"/api/v1/departments/{uuid.uuid4()}", json={"name": "X"}, headers=h
        )
        assert resp.status_code == 404, resp.text


async def test_empty_name_is_rejected(app_session: AppSessionFactory) -> None:
    tenant, dept_id = await _seed_department(app_session)
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.patch(f"/api/v1/departments/{dept_id}", json={"name": ""}, headers=h)
        assert resp.status_code == 422, resp.text


async def test_prompt_caching_enabled_defaults_true_on_get(
    app_session: AppSessionFactory,
) -> None:
    tenant, dept_id = await _seed_department(app_session)
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.get(f"/api/v1/departments/{dept_id}", headers=h)
        assert resp.status_code == 200, resp.text
        assert resp.json()["promptCachingEnabled"] is True


async def test_prompt_caching_enabled_can_be_toggled_off(
    app_session: AppSessionFactory,
) -> None:
    tenant, dept_id = await _seed_department(app_session)
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.patch(
            f"/api/v1/departments/{dept_id}",
            json={"promptCachingEnabled": False},
            headers=h,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["promptCachingEnabled"] is False

        get_resp = await http.get(f"/api/v1/departments/{dept_id}", headers=h)
        assert get_resp.json()["promptCachingEnabled"] is False


async def test_omitting_prompt_caching_enabled_leaves_it_unchanged(
    app_session: AppSessionFactory,
) -> None:
    tenant, dept_id = await _seed_department(app_session)
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        await http.patch(
            f"/api/v1/departments/{dept_id}", json={"promptCachingEnabled": False}, headers=h
        )
        # A later PATCH that doesn't mention the field must not silently
        # reset it back to the default -- same None-means-unchanged
        # convention as name/goal/icon in this same request model.
        resp = await http.patch(
            f"/api/v1/departments/{dept_id}", json={"name": "Renamed"}, headers=h
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["promptCachingEnabled"] is False
