from __future__ import annotations

import base64
import uuid

import pytest

from oc8.oauth.client import resolve_client
from oc8.oauth.errors import OAuthNotConfigured
from oc8.secrets.service import store_secret
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )


@pytest.fixture(autouse=True)
def _no_platform_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    from oc8 import config

    s = config.get_settings()
    monkeypatch.setattr(s, "google_oauth_client_id", "", raising=False)
    monkeypatch.setattr(s, "google_oauth_client_secret", "", raising=False)


def _set_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    from oc8 import config

    s = config.get_settings()
    monkeypatch.setattr(s, "google_oauth_client_id", "plat-id", raising=False)
    monkeypatch.setattr(s, "google_oauth_client_secret", "plat-secret", raising=False)


async def test_tenant_secrets_win_over_platform(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_platform(monkeypatch)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await store_secret(db, tenant_id=tenant, name="oauth/google/client_id", value="ten-id")
        await store_secret(
            db, tenant_id=tenant, name="oauth/google/client_secret", value="ten-secret"
        )
        got = await resolve_client(db, tenant_id=tenant, provider_id="google")
        assert got.client_id == "ten-id"
        assert got.client_secret == "ten-secret"
        assert got.source == "tenant"


async def test_platform_is_the_fallback(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_platform(monkeypatch)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        got = await resolve_client(db, tenant_id=tenant, provider_id="google")
        assert got.client_id == "plat-id"
        assert got.source == "platform"


async def test_neither_configured_raises(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        with pytest.raises(OAuthNotConfigured, match="oauth/google/client_id"):
            await resolve_client(db, tenant_id=tenant, provider_id="google")


async def test_half_configured_tenant_falls_through_to_platform(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only the id stored, no secret -> must NOT produce a half-built tenant client.
    _set_platform(monkeypatch)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await store_secret(db, tenant_id=tenant, name="oauth/google/client_id", value="ten-id")
        got = await resolve_client(db, tenant_id=tenant, provider_id="google")
        assert got.source == "platform"
        assert got.client_id == "plat-id"


async def test_pinned_platform_ignores_tenant_secrets(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A refresh pins to the source that minted the token. Tenant creds added
    # after connecting must not hijack an existing connection's refresh.
    _set_platform(monkeypatch)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await store_secret(db, tenant_id=tenant, name="oauth/google/client_id", value="ten-id")
        await store_secret(
            db, tenant_id=tenant, name="oauth/google/client_secret", value="ten-secret"
        )
        got = await resolve_client(
            db, tenant_id=tenant, provider_id="google", pinned_source="platform"
        )
        assert got.source == "platform"
        assert got.client_id == "plat-id"


async def test_pinned_tenant_missing_raises(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        with pytest.raises(OAuthNotConfigured):
            await resolve_client(
                db, tenant_id=tenant, provider_id="google", pinned_source="tenant"
            )
