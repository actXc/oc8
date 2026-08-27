from __future__ import annotations

import base64
import json
import uuid

import httpx
import jwt
import pytest

from oc8.oauth import http as oauth_http
from oc8.oauth.errors import OAuthError
from oc8.oauth.provisioning import provision_oauth_connection, provisioned_fields
from tests.conftest import AppSessionFactory
from tests.oauth._keys import TEST_PRIVATE_KEY_PEM

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_provision_google` calls `store_secret`, which needs a configured root
    KEK -- mirrors `test_client_credentials.py`'s own fixture of the same
    name for the Microsoft provisioner's equivalent tests."""
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )


_KEY_JSON = json.dumps(
    {
        "type": "service_account",
        "client_email": "svc@my-project.iam.gserviceaccount.com",
        "private_key": TEST_PRIVATE_KEY_PEM,
    }
)


def _mock_transport(*, token_status: int = 200, drive_status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com/token" in str(request.url):
            if token_status != 200:
                return httpx.Response(token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "svc-token", "expires_in": 3600})
        if "drive/v3/drives/" in str(request.url):
            return httpx.Response(drive_status, json={"id": "shared-drive-1"})
        raise AssertionError(f"unexpected request: {request.url}")

    return httpx.MockTransport(handler)


async def test_provisions_a_new_google_service_account_connection(
    app_session: AppSessionFactory,
) -> None:
    oauth_http.set_transport_override(_mock_transport())
    tenant = uuid.uuid4()
    try:
        async with app_session(tenant) as db:
            conn = await provision_oauth_connection(
                db,
                tenant_id=tenant,
                provider="google",
                values={
                    "service_account_key": _KEY_JSON,
                    "shared_drive_ids": "shared-drive-1,shared-drive-2",
                    "delegated_mailboxes": "",
                },
            )
            assert conn.provider == "google"
            assert conn.grant_type == "service_account"
            assert conn.account_label == "svc@my-project.iam.gserviceaccount.com"
    finally:
        oauth_http.set_transport_override(None)


async def test_rejects_a_bad_service_account_key(app_session: AppSessionFactory) -> None:
    oauth_http.set_transport_override(_mock_transport(token_status=400))
    tenant = uuid.uuid4()
    try:
        async with app_session(tenant) as db:
            with pytest.raises(OAuthError):
                await provision_oauth_connection(
                    db,
                    tenant_id=tenant,
                    provider="google",
                    values={
                        "service_account_key": _KEY_JSON,
                        "shared_drive_ids": "shared-drive-1",
                        "delegated_mailboxes": "",
                    },
                )
    finally:
        oauth_http.set_transport_override(None)


async def test_rejects_malformed_key_json(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        with pytest.raises(ValueError, match="service_account_key"):
            await provision_oauth_connection(
                db,
                tenant_id=tenant,
                provider="google",
                values={
                    "service_account_key": "not json",
                    "shared_drive_ids": "",
                    "delegated_mailboxes": "",
                },
            )


def test_provisioned_fields_covers_every_field_provision_oauth_connection_reads() -> None:
    """`provisioned_fields` tells `configure_plugin`'s generic password-field
    loop which submitted fields THIS provisioner already consumes itself (so
    it does not also write a second, unread `plugin:{name}:{key}` copy of a
    long-lived credential -- see `oauth/provisioning.py`'s own module
    docstring). `_GOOGLE_FIELDS` is the real source of truth for that set --
    match it, don't assume only the password field counts."""
    from oc8.oauth.provisioning import _GOOGLE_FIELDS

    assert provisioned_fields("google") == frozenset(_GOOGLE_FIELDS)
    assert provisioned_fields("google") == {
        "service_account_key",
        "shared_drive_ids",
        "delegated_mailboxes",
    }


def _token_and_gmail_transport(
    *, bad_mailboxes: frozenset[str] = frozenset(), delegated_subjects: list[str] | None = None
) -> httpx.MockTransport:
    """Token endpoint always mints; the Gmail profile probe 403s for any mailbox
    in `bad_mailboxes` (what a deleted/suspended/misspelled address looks like
    once a token was mintable for it). `delegated_subjects` collects the `sub`
    claim of every DELEGATED mint that actually went out to Google -- counting
    all token calls would not distinguish a delegated re-mint from the
    self-identity re-mint this provisioner does anyway."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            if delegated_subjects is not None:
                body = dict(p.split("=", 1) for p in request.content.decode().split("&"))
                claims = jwt.decode(body["assertion"], options={"verify_signature": False})
                if "sub" in claims:
                    delegated_subjects.append(claims["sub"])
            return httpx.Response(200, json={"access_token": "svc-token", "expires_in": 3600})
        if "drive/v3/drives/" in url:
            return httpx.Response(200, json={"id": "shared-drive-1"})
        if "gmail.googleapis.com" in url:
            mailbox = url.split("/users/")[1].split("/")[0]
            if mailbox in bad_mailboxes:
                return httpx.Response(403, json={"error": "unauthorized"})
            return httpx.Response(200, json={"emailAddress": mailbox})
        raise AssertionError(f"unexpected request: {request.url}")

    return httpx.MockTransport(handler)


async def test_setup_probes_every_delegated_mailbox_not_just_the_first(
    app_session: AppSessionFactory,
) -> None:
    """Whole-branch review M5. Domain-wide delegation is authorized per service
    account and scope, so probing ONE mailbox proves delegation -- it does not
    prove the other addresses exist or are spelled correctly. Combined with C1,
    a typo at index >= 1 used to be introduced behind a fully green setup screen
    and only surface later. This drives a bad address at index 1 specifically,
    because index 0 was already covered."""
    oauth_http.set_transport_override(
        _token_and_gmail_transport(bad_mailboxes=frozenset({"typo@company.com"}))
    )
    tenant = uuid.uuid4()
    try:
        async with app_session(tenant) as db:
            with pytest.raises(OAuthError) as caught:
                await provision_oauth_connection(
                    db,
                    tenant_id=tenant,
                    provider="google",
                    values={
                        "service_account_key": _KEY_JSON,
                        "shared_drive_ids": "",
                        "delegated_mailboxes": "good@company.com, typo@company.com",
                    },
                )
        assert "typo@company.com" in str(caught.value)
    finally:
        oauth_http.set_transport_override(None)


async def test_a_first_mailbox_that_works_does_not_green_light_the_rest(
    app_session: AppSessionFactory,
) -> None:
    """The mirror of the above: the good address really is accepted, so the
    failure above is about the SECOND mailbox and not about the probe having
    become indiscriminate."""
    oauth_http.set_transport_override(_token_and_gmail_transport())
    tenant = uuid.uuid4()
    try:
        async with app_session(tenant) as db:
            conn = await provision_oauth_connection(
                db,
                tenant_id=tenant,
                provider="google",
                values={
                    "service_account_key": _KEY_JSON,
                    "shared_drive_ids": "",
                    "delegated_mailboxes": "good@company.com, also-good@company.com",
                },
            )
            assert conn.grant_type == "service_account"
    finally:
        oauth_http.set_transport_override(None)


async def test_the_self_identity_scope_is_recorded_on_the_connection(
    app_session: AppSessionFactory,
) -> None:
    """I4. `oauth/tokens.py` is shared grant machinery for every provider and
    must not carry a Google URL; the provisioner writes the provider's own
    self-identity scope onto the row, and `_mint_service_account` reads it
    there. `scopes` used to be provisioned as `[]`."""
    oauth_http.set_transport_override(_mock_transport())
    tenant = uuid.uuid4()
    try:
        async with app_session(tenant) as db:
            conn = await provision_oauth_connection(
                db,
                tenant_id=tenant,
                provider="google",
                values={
                    "service_account_key": _KEY_JSON,
                    "shared_drive_ids": "shared-drive-1",
                    "delegated_mailboxes": "",
                },
            )
            assert conn.scopes == ["https://www.googleapis.com/auth/drive"]
    finally:
        oauth_http.set_transport_override(None)


async def test_rotating_the_key_re_verifies_delegation_against_the_new_key(
    app_session: AppSessionFactory,
) -> None:
    """I3's headline consequence. docs/GOOGLE_WORKSPACE.md tells an admin to
    rotate a leaked service-account key by resubmitting this form. The row is
    reused (same connection id), and delegated tokens are cached per
    connection+subject in-process -- so without an explicit invalidation the
    resubmission's own delegated probe hits the cache and reports success using
    a token minted from the OLD private key. The one flow whose entire purpose
    is to verify the new key would verify nothing."""
    delegated_mints: list[str] = []
    oauth_http.set_transport_override(
        _token_and_gmail_transport(delegated_subjects=delegated_mints)
    )
    tenant = uuid.uuid4()
    values = {
        "service_account_key": _KEY_JSON,
        "shared_drive_ids": "",
        "delegated_mailboxes": "agents@company.com",
    }
    try:
        async with app_session(tenant) as db:
            first = await provision_oauth_connection(
                db, tenant_id=tenant, provider="google", values=values
            )
            first_id = first.id
            assert delegated_mints == ["agents@company.com"]

            second = await provision_oauth_connection(
                db, tenant_id=tenant, provider="google", values=values
            )
            assert second.id == first_id, "a resubmission must reuse the same row"
            # A SECOND delegated mint, against the freshly stored key -- not a
            # replay of the cached pre-rotation token. Counting all token calls
            # would not show this: the self-identity token re-mints on every
            # submission anyway, via the `expires_at = None` above.
            assert delegated_mints == ["agents@company.com", "agents@company.com"]
    finally:
        oauth_http.set_transport_override(None)
