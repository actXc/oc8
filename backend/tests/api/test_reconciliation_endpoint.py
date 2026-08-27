"""POST /model-cost-reconciliation/refresh -- resolves an admin key if one is
stored, fetches the provider's reported cost, upserts + returns the
comparison. No admin key stored -> that provider is silently skipped (it is
opt-in), not an error."""

from __future__ import annotations

import base64
import datetime as dt
import uuid

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import config
from oc8.auth import get_identity_provider
from oc8.main import create_app
from oc8.secrets.service import store_secret
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _token(tenant: uuid.UUID, role: str = "org_admin") -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role=role)


async def test_refresh_with_no_admin_key_returns_empty(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant):
        pass
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.post("/api/v1/model-cost-reconciliation/refresh", headers=headers)
            assert r.status_code == 200, r.text
            assert r.json() == []


async def test_refresh_with_stored_admin_key_fetches_and_upserts(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.uuid4()
    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )

    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, json={"data": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with app_session(tenant) as db:
        await store_secret(
            db, tenant_id=tenant, name="model/anthropic/admin_key", value="admin-key-123"
        )
        await db.commit()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.post("/api/v1/model-cost-reconciliation/refresh", headers=headers)
            assert r.status_code == 200, r.text
            # Empty provider response -> no rows to upsert, but the call must not error.
            assert r.json() == []


async def test_refresh_isolates_provider_fetch_failures(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A present-but-broken admin key for one provider (e.g. expired/revoked,
    surfaced by the provider as a non-2xx response) must not block the other
    provider's fetch, upsert, or the final commit -- it should be logged and
    skipped, not fatal to the whole request."""
    tenant = uuid.uuid4()
    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )

    today = dt.date.today()
    start_time = int(dt.datetime.combine(today, dt.time.min, tzinfo=dt.UTC).timestamp())
    real_get = httpx.AsyncClient.get

    async def fake_get(self, url, **kwargs):
        if "anthropic.com" in str(url):
            # A rejected/expired admin key: a real 401 from the provider,
            # which fetch_anthropic_cost turns into httpx.HTTPStatusError.
            return httpx.Response(
                401,
                json={"error": {"message": "invalid x-api-key"}},
                request=httpx.Request("GET", url),
            )
        if "openai.com" in str(url):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "start_time": start_time,
                            "results": [{"amount": {"value": 12.34, "currency": "usd"}}],
                        }
                    ]
                },
                request=httpx.Request("GET", url),
            )
        # Not a provider call -- this is the test's own AsyncClient hitting
        # the app via ASGITransport (e.g. the GET below); let it through.
        return await real_get(self, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with app_session(tenant) as db:
        await store_secret(db, tenant_id=tenant, name="model/anthropic/admin_key", value="bad-key")
        await store_secret(db, tenant_id=tenant, name="model/openai/admin_key", value="good-key")
        await db.commit()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.post("/api/v1/model-cost-reconciliation/refresh", headers=headers)
            assert r.status_code == 200, r.text
            body = r.json()
            providers = {row["provider"] for row in body}
            # Anthropic's broken key was skipped (logged, not fatal); openai's
            # working fetch still went through and is present in the response.
            assert providers == {"openai"}
            assert body[0]["providerReportedCostMicros"] == 12_340_000

            # The openai row must actually be persisted (commit not aborted by
            # the anthropic failure raised earlier in the same request).
            listed = await client.get("/api/v1/model-cost-reconciliation", headers=headers)
            assert listed.status_code == 200, listed.text
            listed_providers = {row["provider"] for row in listed.json()}
            assert listed_providers == {"openai"}


async def test_refresh_forbidden_for_non_admin() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant, role='member')}"}
            r = await client.post("/api/v1/model-cost-reconciliation/refresh", headers=headers)
            assert r.status_code == 403, r.text
