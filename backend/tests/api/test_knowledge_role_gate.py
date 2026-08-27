from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8.auth import get_identity_provider
from oc8.main import create_app

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID, role: str) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="s", role=role)
    return {"Authorization": f"Bearer {token}"}


async def test_members_cannot_create_sources_bases_or_sync() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            h = _headers(tenant, "member")
            sid = uuid.uuid4()
            assert (
                await c.post(
                    "/api/v1/knowledge/sources",
                    json={
                        "connectorType": "website",
                        "name": "x",
                        "config": {"url": "https://example.com"},
                        "kbId": str(uuid.uuid4()),
                    },
                    headers=h,
                )
            ).status_code == 403
            assert (
                await c.post("/api/v1/knowledge/bases", json={"name": "kb"}, headers=h)
            ).status_code == 403
            assert (
                await c.post(
                    f"/api/v1/knowledge/sources/{sid}/sync",
                    json={"kbId": str(uuid.uuid4())},
                    headers=h,
                )
            ).status_code == 403
            assert (
                await c.post(f"/api/v1/knowledge/sources/{sid}/preview", json={}, headers=h)
            ).status_code == 403
            assert (
                await c.post(
                    "/api/v1/knowledge/grants",
                    json={
                        "kbId": str(uuid.uuid4()),
                        "granteeType": "department",
                        "granteeId": str(uuid.uuid4()),
                    },
                    headers=h,
                )
            ).status_code == 403
            assert (
                await c.delete(
                    f"/api/v1/knowledge/bases/{uuid.uuid4()}/sources/{sid}", headers=h
                )
            ).status_code == 403


async def test_agents_cannot_create_sources() -> None:
    # The concrete risk this gate closes: an agent principal triggering a sync
    # against a customer's connected Drive.
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/knowledge/sources",
                json={
                    "connectorType": "website",
                    "name": "x",
                    "config": {"url": "https://example.com"},
                    "kbId": str(uuid.uuid4()),
                },
                headers=_headers(tenant, "agent"),
            )
            assert r.status_code == 403, r.text


async def test_reads_stay_open_to_a_non_admin_role() -> None:
    """A non-admin may still read the knowledge surfaces -- that was the point of
    this file and it has not changed. What HAS changed is which principals count
    as non-admin: `knowledge:view` is held by operator, dept_manager and auditor,
    so the intent is expressed by naming a real role rather than by admitting
    anything that carries a token."""
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            h = _headers(tenant, "operator")
            assert (await c.get("/api/v1/knowledge/bases", headers=h)).status_code == 200
            assert (await c.get("/api/v1/knowledge/sources", headers=h)).status_code == 200


async def test_an_unrecognised_role_now_reads_nothing() -> None:
    """A deliberate narrowing, and the reason the permission layer is worth
    having. Reads used to be open to ANY authenticated principal, so a token
    whose role claim was a typo -- or an IdP group nobody had mapped yet -- saw
    everything a legitimate operator saw. An unknown role now holds no
    permissions at all, which is the safe direction to be wrong in: the symptom
    is an empty screen and a 403 an operator can report, not silent access."""
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            h = _headers(tenant, "member")  # issued by nothing, mapped by nothing
            assert (await c.get("/api/v1/knowledge/bases", headers=h)).status_code == 403
            assert (await c.get("/api/v1/knowledge/sources", headers=h)).status_code == 403


async def test_admins_can_still_write() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/knowledge/bases",
                json={"name": "kb-admin"},
                headers=_headers(tenant, "org_admin"),
            )
            assert r.status_code == 201, r.text
