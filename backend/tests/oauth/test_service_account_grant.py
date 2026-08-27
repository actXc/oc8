from __future__ import annotations

import base64
import uuid

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from oc8 import models as m
from oc8.oauth import http as oauth_http
from oc8.oauth.errors import OAuthError, OAuthReauthRequired
from oc8.oauth.tokens import (
    access_ref,
    get_access_token,
    invalidate_delegated_tokens,
    mint_delegated_token,
    refresh_ref,
)
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    """Secret store needs a KEK configured to encrypt/decrypt the service
    account's stored private key. Mirrors test_client_credentials.py's
    identical fixture for the Microsoft plugin's client_credentials grant."""
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )


def _private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


async def _make_connection(db, tenant_id: uuid.UUID, private_key_pem: str) -> m.OAuthConnection:
    conn = m.OAuthConnection(
        tenant_id=tenant_id,
        provider="google",
        account_label="svc@my-project.iam.gserviceaccount.com",
        # What `_provision_google` writes. `_mint_service_account` mints the
        # self-identity token with the scope off THIS column rather than a
        # hardcoded Google URL in shared core (whole-branch review, I4), so a
        # row built by hand has to carry it too.
        scopes=["https://www.googleapis.com/auth/drive"],
        access_secret_ref=access_ref(uuid.uuid4()),
        client_source="tenant",
        grant_type="service_account",
        status="active",
    )
    db.add(conn)
    await db.flush()
    conn.access_secret_ref = access_ref(conn.id)
    conn.refresh_secret_ref = refresh_ref(conn.id)
    from oc8.secrets.service import store_secret

    await store_secret(
        db,
        tenant_id=tenant_id,
        name=conn.refresh_secret_ref,
        value=private_key_pem,
        kind="oauth_token",
    )
    await db.flush()
    return conn


async def test_get_access_token_mints_a_self_identity_token_with_no_sub_claim(
    app_session: AppSessionFactory,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = dict(p.split("=") for p in request.content.decode().split("&"))
        assertion = body["assertion"]
        claims = jwt.decode(assertion, options={"verify_signature": False})
        captured["claims"] = claims
        return httpx.Response(200, json={"access_token": "svc-token-1", "expires_in": 3600})

    oauth_http.set_transport_override(httpx.MockTransport(handler))
    tenant = uuid.uuid4()
    try:
        async with app_session(tenant) as db:
            conn = await _make_connection(db, tenant, _private_key_pem())
            await db.flush()
            token = await get_access_token(db, tenant_id=tenant, connection_id=conn.id)
            assert token == "svc-token-1"
            claims = captured["claims"]
            assert claims["iss"] == "svc@my-project.iam.gserviceaccount.com"
            assert "sub" not in claims
            # The scope is the connection's own, not a constant in core.
            assert claims["scope"] == "https://www.googleapis.com/auth/drive"
    finally:
        oauth_http.set_transport_override(None)


async def test_a_service_account_row_with_no_scopes_says_so_instead_of_guessing(
    app_session: AppSessionFactory,
) -> None:
    """I4. `_SELF_IDENTITY_SCOPE` used to be a Google Drive URL hardcoded in
    `oauth/tokens.py` -- shared grant machinery for every provider. The second
    `service_account` provider to arrive would have been handed a Google scope
    and discovered it as an opaque far-system rejection. The scope now comes
    off `OAuthConnection.scopes`, which the per-provider provisioner writes; a
    row without one is a configuration error, and it says so HERE rather than
    silently substituting somebody else's vendor's scope."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        conn = await _make_connection(db, tenant, _private_key_pem())
        conn.scopes = []
        await db.flush()
        with pytest.raises(OAuthError, match="no scopes recorded"):
            await get_access_token(db, tenant_id=tenant, connection_id=conn.id)


_DELEGATED_SCOPE = (
    "https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/calendar"
)


async def test_mint_delegated_token_adds_a_sub_claim_and_caches_per_connection_and_subject(
    app_session: AppSessionFactory,
) -> None:
    """A connection with N delegated mailboxes gets a brand-new, un-pooled MCP
    bridge PER TOOL CALL (mcp_pool.py's reusable=False for any oauth-* ref, see
    Task 3) -- so an uncached mint would pay one HTTP round trip per configured
    mailbox on every single tool call, including ones that touch none of them.
    `mint_delegated_token` caches per (connection_id, subject) instead."""
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = dict(p.split("=") for p in request.content.decode().split("&"))
        claims = jwt.decode(body["assertion"], options={"verify_signature": False})
        calls.append(claims)
        return httpx.Response(
            200, json={"access_token": f"delegated-{len(calls)}", "expires_in": 3600}
        )

    oauth_http.set_transport_override(httpx.MockTransport(handler))
    tenant = uuid.uuid4()
    try:
        async with app_session(tenant) as db:
            conn = await _make_connection(db, tenant, _private_key_pem())
            await db.flush()
            token1 = await mint_delegated_token(
                db,
                tenant_id=tenant,
                connection_id=conn.id,
                subject="agents@company.com",
                scope=_DELEGATED_SCOPE,
            )
            token2 = await mint_delegated_token(
                db,
                tenant_id=tenant,
                connection_id=conn.id,
                subject="agents@company.com",
                scope=_DELEGATED_SCOPE,
            )
            token3 = await mint_delegated_token(
                db,
                tenant_id=tenant,
                connection_id=conn.id,
                subject="other@company.com",
                scope=_DELEGATED_SCOPE,
            )
            assert calls[0]["sub"] == "agents@company.com"
            # Same (connection, subject) twice -> ONE HTTP round trip, cache hit the 2nd time.
            assert len(calls) == 2
            assert token1 == token2 == "delegated-1"
            # A DIFFERENT subject on the same connection -> its own cache entry, its own mint.
            assert token3 == "delegated-2"
            assert calls[1]["sub"] == "other@company.com"
    finally:
        oauth_http.set_transport_override(None)


async def test_mint_delegated_token_remints_after_the_cached_token_expires(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oc8.oauth.tokens as tokens_module

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"access_token": f"tok-{calls}", "expires_in": 3600})

    oauth_http.set_transport_override(httpx.MockTransport(handler))
    tenant = uuid.uuid4()
    try:
        async with app_session(tenant) as db:
            conn = await _make_connection(db, tenant, _private_key_pem())
            await db.flush()
            monkeypatch.setattr(
                tokens_module, "_JWT_LIFETIME_SECONDS", -10
            )  # force immediate expiry
            token1 = await mint_delegated_token(
                db,
                tenant_id=tenant,
                connection_id=conn.id,
                subject="a@b.com",
                scope=_DELEGATED_SCOPE,
            )
            token2 = await mint_delegated_token(
                db,
                tenant_id=tenant,
                connection_id=conn.id,
                subject="a@b.com",
                scope=_DELEGATED_SCOPE,
            )
            assert calls == 2
            assert token1 == "tok-1"
            assert token2 == "tok-2"
    finally:
        oauth_http.set_transport_override(None)


async def test_mint_delegated_token_rejects_a_connection_that_is_not_service_account(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        conn = m.OAuthConnection(
            tenant_id=tenant,
            provider="microsoft",
            account_label="some-client-id",
            access_secret_ref=access_ref(uuid.uuid4()),
            client_source="tenant",
            grant_type="client_credentials",
            status="active",
        )
        db.add(conn)
        await db.flush()
        with pytest.raises(ValueError, match="service_account"):
            await mint_delegated_token(
                db,
                tenant_id=tenant,
                connection_id=conn.id,
                subject="x@y.com",
                scope=_DELEGATED_SCOPE,
            )


async def test_invalidating_forces_the_next_delegated_mint_to_go_back_to_google(
    app_session: AppSessionFactory,
) -> None:
    """I3, the mechanism. `docs/GOOGLE_WORKSPACE.md` tells an admin to rotate a
    leaked service-account key by resubmitting the setup form. Google access
    tokens outlive the key that minted them, so a cache that nothing clears
    keeps handing back a token minted from the PREVIOUS private key -- and the
    resubmission's own delegated probe then reports success for a rotation it
    never actually verified. `invalidate_delegated_tokens` is what
    `_provision_google` calls when key material is re-stored (and what
    `disconnect` calls when the row is deleted)."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"access_token": f"tok-{calls}", "expires_in": 3600})

    oauth_http.set_transport_override(httpx.MockTransport(handler))
    tenant = uuid.uuid4()
    try:
        async with app_session(tenant) as db:
            conn = await _make_connection(db, tenant, _private_key_pem())
            await db.flush()
            first = await mint_delegated_token(
                db,
                tenant_id=tenant,
                connection_id=conn.id,
                subject="a@b.com",
                scope=_DELEGATED_SCOPE,
            )
            cached = await mint_delegated_token(
                db,
                tenant_id=tenant,
                connection_id=conn.id,
                subject="a@b.com",
                scope=_DELEGATED_SCOPE,
            )
            assert first == cached == "tok-1"
            assert calls == 1

            invalidate_delegated_tokens(conn.id)
            after = await mint_delegated_token(
                db,
                tenant_id=tenant,
                connection_id=conn.id,
                subject="a@b.com",
                scope=_DELEGATED_SCOPE,
            )
            assert after == "tok-2", "the pre-rotation token was served again"
            assert calls == 2
    finally:
        oauth_http.set_transport_override(None)


async def test_a_cache_hit_still_re_checks_that_the_connection_is_active(
    app_session: AppSessionFactory,
) -> None:
    """I3, the ordering. The cache lookup used to return BEFORE
    `_live_connection` ran, so for the life of a cached entry neither the
    tenant-ownership check nor the `status == "active"` check happened at all
    -- the sibling `get_access_token` has always validated first."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "delegated", "expires_in": 3600})

    oauth_http.set_transport_override(httpx.MockTransport(handler))
    tenant = uuid.uuid4()
    try:
        async with app_session(tenant) as db:
            conn = await _make_connection(db, tenant, _private_key_pem())
            await db.flush()
            assert (
                await mint_delegated_token(
                    db,
                    tenant_id=tenant,
                    connection_id=conn.id,
                    subject="a@b.com",
                    scope=_DELEGATED_SCOPE,
                )
                == "delegated"
            )
            conn.status = "revoked"
            await db.flush()
            with pytest.raises(OAuthReauthRequired):
                await mint_delegated_token(
                    db,
                    tenant_id=tenant,
                    connection_id=conn.id,
                    subject="a@b.com",
                    scope=_DELEGATED_SCOPE,
                )
    finally:
        oauth_http.set_transport_override(None)


async def test_a_cached_delegated_token_is_not_served_to_another_tenant(
    app_session: AppSessionFactory,
) -> None:
    """I3, the key. `_delegated_token_cache` is process-wide and was keyed on
    `(connection_id, subject)` only, with the tenant compared nowhere on a hit."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "tenant-a-token", "expires_in": 3600})

    oauth_http.set_transport_override(httpx.MockTransport(handler))
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    try:
        async with app_session(tenant_a) as db:
            conn = await _make_connection(db, tenant_a, _private_key_pem())
            await db.flush()
            connection_id = conn.id
            await mint_delegated_token(
                db,
                tenant_id=tenant_a,
                connection_id=connection_id,
                subject="a@b.com",
                scope=_DELEGATED_SCOPE,
            )
        async with app_session(tenant_b) as db:
            with pytest.raises(OAuthReauthRequired):
                await mint_delegated_token(
                    db,
                    tenant_id=tenant_b,
                    connection_id=connection_id,
                    subject="a@b.com",
                    scope=_DELEGATED_SCOPE,
                )
    finally:
        oauth_http.set_transport_override(None)
