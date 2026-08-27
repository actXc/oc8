"""POST /onboarding/complete and /onboarding/skip -- how the gamified
onboarding wizard (design: gamified-onboarding-wizard-design.md) permanently
stops its own /welcome redirect. Two distinct routes rather than one
parameterized route, same reasoning as useClarifications in hooks.ts: no
parameter here to get wrong."""

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


async def _seed_org(app_session: AppSessionFactory) -> uuid.UUID:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(m.Organization(id=tenant, slug=f"t-{tenant.hex[:8]}", name="T", settings={}))
        await db.flush()
    return tenant


async def test_complete_sets_status_completed(app_session: AppSessionFactory) -> None:
    tenant = await _seed_org(app_session)
    async with _http() as http:
        resp = await http.post(
            "/api/v1/onboarding/complete", headers=_headers(tenant, "org_admin")
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["onboardingStatus"] == "completed"


async def test_skip_sets_status_skipped(app_session: AppSessionFactory) -> None:
    tenant = await _seed_org(app_session)
    async with _http() as http:
        resp = await http.post("/api/v1/onboarding/skip", headers=_headers(tenant, "org_admin"))
        assert resp.status_code == 200, resp.text
        assert resp.json()["onboardingStatus"] == "skipped"


async def test_member_role_is_refused_on_both_routes(app_session: AppSessionFactory) -> None:
    tenant = await _seed_org(app_session)
    async with _http() as http:
        h = _headers(tenant, "member")
        assert (await http.post("/api/v1/onboarding/complete", headers=h)).status_code == 403
        assert (await http.post("/api/v1/onboarding/skip", headers=h)).status_code == 403
