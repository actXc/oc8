"""Tests for the ChatGPT subscription device-code login endpoints (chatgpt
subscription auth plan, Task 5): `POST /models/chatgpt-subscription/device/start`
and `POST /models/chatgpt-subscription/device/poll`.

Fixture note: `tests/api/test_agent_model_switch.py` and
`tests/api/test_oauth_service_connection.py` -- the two existing files this
suite is closest to -- do NOT use `client`/`auth_headers`/`db_session`/
`tenant_id` fixtures; there is no such conftest fixture in this repo. Each
test builds its own `create_app()` + `AsyncClient` and mints its own bearer
token via `get_identity_provider().mint(...)`, matching those two files'
established pattern.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8.auth import get_identity_provider
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app
from oc8.oauth.device_flow import DeviceLoginStart, DevicePollResult
from oc8.oauth.errors import OAuthExchangeFailed
from oc8.oauth.tokens import TokenResponse
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

# A synthetic, unsigned JWT (alg "none") carrying OpenAI's
# `https://api.openai.com/auth.chatgpt_account_id` claim -- exactly the shape
# `extract_chatgpt_account_id` (Task 4) decodes without signature
# verification. Decodes to {"https://api.openai.com/auth":
# {"chatgpt_account_id": "acct_xyz"}}.
_ID_TOKEN = (
    "eyJhbGciOiJub25lIn0."
    "eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOnsiY2hhdGdwdF9hY2NvdW50X2lkIjoiYWNjdF94eXoifX0."
)


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    """The "complete" poll path writes tokens into the §12.3 secret store
    (`persist_tokens`), which needs a valid KEK to encrypt with -- same
    autouse fixture as `test_oauth_service_connection.py`."""
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )


def _token(role: str = "org_admin") -> str:
    return get_identity_provider().mint(
        tenant_id=uuid.UUID(str(ACME_TENANT_ID)), subject="admin-user", role=role
    )


async def test_start_returns_device_auth_id() -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token()}"}
            with patch(
                "oc8.api.v1.catalog.start_device_login",
                AsyncMock(
                    return_value=DeviceLoginStart(
                        device_auth_id="da1",
                        user_code="ABCD-1234",
                        verification_uri="https://auth.openai.com/codex/device",
                        expires_in=900,
                        interval=5,
                    )
                ),
            ):
                resp = await client.post(
                    "/api/v1/models/chatgpt-subscription/device/start", headers=headers
                )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["userCode"] == "ABCD-1234"
    assert body["deviceAuthId"] == "da1"
    assert body["interval"] == 5


async def test_start_returns_clean_error_when_openai_rejects_device_login() -> None:
    """The natural companion to `test_poll_error_status_is_passed_through`:
    `start_device_login()` raises `OAuthExchangeFailed` for a documented,
    realistic failure (device-code sign-in disabled for the account, any
    other 4xx/5xx, a non-JSON or malformed response -- see
    `device_flow.start_device_login`'s own docstring/body). The endpoint must
    turn that into a clean HTTP error, not let it escape as a bare 500."""
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token()}"}
            with patch(
                "oc8.api.v1.catalog.start_device_login",
                AsyncMock(
                    side_effect=OAuthExchangeFailed(
                        "device-code sign-in is not enabled on this ChatGPT account"
                    )
                ),
            ):
                resp = await client.post(
                    "/api/v1/models/chatgpt-subscription/device/start", headers=headers
                )
    assert resp.status_code == 400, resp.text
    assert "device-code sign-in is not enabled" in resp.json()["detail"]


async def test_start_requires_model_manage_permission() -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(role='member')}"}
            resp = await client.post(
                "/api/v1/models/chatgpt-subscription/device/start", headers=headers
            )
    assert resp.status_code == 403


async def test_poll_pending_returns_pending_status() -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token()}"}
            future = (datetime.now(tz=UTC) + timedelta(minutes=10)).isoformat()
            with patch(
                "oc8.api.v1.catalog.poll_device_login",
                AsyncMock(return_value=DevicePollResult(status="pending", tokens=None, error=None)),
            ):
                resp = await client.post(
                    "/api/v1/models/chatgpt-subscription/device/poll",
                    json={"deviceAuthId": "da1", "userCode": "ABCD-1234", "expiresAt": future},
                    headers=headers,
                )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"


async def test_poll_past_deadline_returns_expired_without_calling_openai() -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token()}"}
            past = (datetime.now(tz=UTC) - timedelta(seconds=1)).isoformat()
            with patch("oc8.api.v1.catalog.poll_device_login", AsyncMock()) as mocked_poll:
                resp = await client.post(
                    "/api/v1/models/chatgpt-subscription/device/poll",
                    json={"deviceAuthId": "da1", "userCode": "ABCD-1234", "expiresAt": past},
                    headers=headers,
                )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "expired"
    mocked_poll.assert_not_awaited()


async def test_poll_error_status_is_passed_through() -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token()}"}
            future = (datetime.now(tz=UTC) + timedelta(minutes=10)).isoformat()
            with patch(
                "oc8.api.v1.catalog.poll_device_login",
                AsyncMock(
                    return_value=DevicePollResult(status="error", tokens=None, error="http 500")
                ),
            ):
                resp = await client.post(
                    "/api/v1/models/chatgpt-subscription/device/poll",
                    json={"deviceAuthId": "da1", "userCode": "ABCD-1234", "expiresAt": future},
                    headers=headers,
                )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"] == "http 500"
    assert body["credentialId"] is None


async def test_poll_complete_creates_connection_and_returns_credential_id() -> None:
    """The full "complete" branch: an OAuthConnection row plus the
    `openai_chatgpt_subscription` Credential that points at it.

    Written against a stub during Task 5 (`create_chatgpt_subscription_credential`
    raised `NotImplementedError` then) and passing since Task 6 landed the
    real credential-creation bridge.
    """
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token()}"}
            future = (datetime.now(tz=UTC) + timedelta(minutes=10)).isoformat()
            with patch(
                "oc8.api.v1.catalog.poll_device_login",
                AsyncMock(
                    return_value=DevicePollResult(
                        status="complete",
                        tokens=TokenResponse(
                            access_token="at1",
                            refresh_token="rt1",
                            expires_in=3600,
                            scope=None,
                            id_token=_ID_TOKEN,
                        ),
                        error=None,
                    )
                ),
            ):
                resp = await client.post(
                    "/api/v1/models/chatgpt-subscription/device/poll",
                    json={"deviceAuthId": "da1", "userCode": "ABCD-1234", "expiresAt": future},
                    headers=headers,
                )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "complete"
    assert body["credentialId"] is not None


def _id_token_for(account_id: str) -> str:
    """An unsigned (alg "none") id_token carrying `chatgpt_account_id`, built
    per-test so each one owns its own account label -- the poll endpoint keys
    a reconnect on that label, and a shared literal would make two tests each
    other's "reconnect"."""
    import json

    def seg(payload: dict[str, object]) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    header = seg({"alg": "none"})
    body = seg({"https://api.openai.com/auth": {"chatgpt_account_id": account_id}})
    return f"{header}.{body}."


def _complete(id_token: str | None, *, access: str, refresh: str) -> AsyncMock:
    return AsyncMock(
        return_value=DevicePollResult(
            status="complete",
            tokens=TokenResponse(
                access_token=access,
                refresh_token=refresh,
                expires_in=3600,
                scope=None,
                id_token=id_token,
            ),
            error=None,
        )
    )


async def _poll(client: AsyncClient, headers: dict[str, str]) -> dict[str, object]:
    future = (datetime.now(tz=UTC) + timedelta(minutes=10)).isoformat()
    resp = await client.post(
        "/api/v1/models/chatgpt-subscription/device/poll",
        json={"deviceAuthId": "da1", "userCode": "ABCD-1234", "expiresAt": future},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, object] = resp.json()
    return body


async def test_signing_in_again_with_the_same_account_reuses_the_connection(
    app_session: AppSessionFactory,
) -> None:
    """Reconnecting is the DOCUMENTED recovery path for this provider --
    refresh tokens rotate and reuse is terminal, so `needs_reauth` leaves
    "sign in again" as the only remedy. It used to 500: the handler always
    built a NEW OAuthConnection, and both `uq_oauth_conn_tenant_provider_account`
    and `uq_credential_tenant_name` collide on the second login, throwing away
    freshly-minted tokens with no in-product way out.
    """
    from sqlalchemy import select

    from oc8 import models as m

    tenant = uuid.uuid4()
    token = get_identity_provider().mint(tenant_id=tenant, subject="admin", role="org_admin")
    headers = {"Authorization": f"Bearer {token}"}
    account = f"acct_{uuid.uuid4().hex[:10]}"
    id_token = _id_token_for(account)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            with patch(
                "oc8.api.v1.catalog.poll_device_login",
                _complete(id_token, access="at1", refresh="rt1"),
            ):
                first = await _poll(client, headers)
            with patch(
                "oc8.api.v1.catalog.poll_device_login",
                _complete(id_token, access="at2", refresh="rt2"),
            ):
                second = await _poll(client, headers)

    assert first["status"] == "complete"
    assert second["status"] == "complete", second
    # Same credential id: every ModelConfig already bound to it keeps working.
    assert second["credentialId"] == first["credentialId"]

    async with app_session(tenant) as db:
        conns = (
            (
                await db.execute(
                    select(m.OAuthConnection).where(m.OAuthConnection.tenant_id == tenant)
                )
            )
            .scalars()
            .all()
        )
        assert len(conns) == 1
        conn = conns[0]
        assert conn.account_label == account
        assert conn.status == "active"
        creds = (
            (await db.execute(select(m.Credential).where(m.Credential.tenant_id == tenant)))
            .scalars()
            .all()
        )
        assert len(creds) == 1
        assert str(creds[0].id) == second["credentialId"]
        # The NEW tokens are what a later refresh will use.
        from oc8.oauth.tokens import access_ref, refresh_ref
        from oc8.secrets.service import resolve_secret

        assert await resolve_secret(db, tenant_id=tenant, ref=access_ref(conn.id)) == "at2"
        assert await resolve_secret(db, tenant_id=tenant, ref=refresh_ref(conn.id)) == "rt2"


async def test_reconnecting_revives_a_needs_reauth_connection(
    app_session: AppSessionFactory,
) -> None:
    """The actual recovery: a connection parked at `needs_reauth` (refresh
    token rotated out from under it) must come back to `active`, or the
    re-login leaves the operator exactly where they started."""
    from sqlalchemy import select

    from oc8 import models as m

    tenant = uuid.uuid4()
    token = get_identity_provider().mint(tenant_id=tenant, subject="admin", role="org_admin")
    headers = {"Authorization": f"Bearer {token}"}
    account = f"acct_{uuid.uuid4().hex[:10]}"
    id_token = _id_token_for(account)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            with patch(
                "oc8.api.v1.catalog.poll_device_login",
                _complete(id_token, access="at1", refresh="rt1"),
            ):
                first = await _poll(client, headers)

            async with app_session(tenant) as db:
                conn = (
                    await db.execute(
                        select(m.OAuthConnection).where(m.OAuthConnection.tenant_id == tenant)
                    )
                ).scalar_one()
                conn.status = "needs_reauth"

            with patch(
                "oc8.api.v1.catalog.poll_device_login",
                _complete(id_token, access="at3", refresh="rt3"),
            ):
                second = await _poll(client, headers)

    assert second["credentialId"] == first["credentialId"]
    async with app_session(tenant) as db:
        conn = (
            await db.execute(select(m.OAuthConnection).where(m.OAuthConnection.tenant_id == tenant))
        ).scalar_one()
        assert conn.status == "active"


async def test_two_logins_without_an_account_id_do_not_collide(
    app_session: AppSessionFactory,
) -> None:
    """With no `chatgpt_account_id` claim the label names no account, so a
    second such login is NOT known to be the same one -- it gets its own row
    under a disambiguated label instead of colliding on the fixed literal."""
    from sqlalchemy import select

    from oc8 import models as m

    tenant = uuid.uuid4()
    token = get_identity_provider().mint(tenant_id=tenant, subject="admin", role="org_admin")
    headers = {"Authorization": f"Bearer {token}"}

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            with patch(
                "oc8.api.v1.catalog.poll_device_login",
                _complete(None, access="at1", refresh="rt1"),
            ):
                first = await _poll(client, headers)
            with patch(
                "oc8.api.v1.catalog.poll_device_login",
                _complete(None, access="at2", refresh="rt2"),
            ):
                second = await _poll(client, headers)

    assert first["status"] == "complete"
    assert second["status"] == "complete", second
    assert second["credentialId"] != first["credentialId"]

    async with app_session(tenant) as db:
        conns = (
            (
                await db.execute(
                    select(m.OAuthConnection).where(m.OAuthConnection.tenant_id == tenant)
                )
            )
            .scalars()
            .all()
        )
        assert len(conns) == 2
        assert len({c.account_label for c in conns}) == 2
        assert all(c.account_label.startswith("ChatGPT subscription") for c in conns)


async def test_poll_rejects_a_malformed_expires_at_with_422_not_500() -> None:
    """`expiresAt` is a real `AwareDatetime` field now, so Pydantic rejects a
    malformed value before the handler runs -- it used to be a bare string
    fed to `datetime.fromisoformat`, i.e. a 500 on an authenticated route."""
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token()}"}
            resp = await client.post(
                "/api/v1/models/chatgpt-subscription/device/poll",
                json={"deviceAuthId": "da1", "userCode": "ABCD-1234", "expiresAt": "not-a-date"},
                headers=headers,
            )
    assert resp.status_code == 422, resp.text


async def test_poll_rejects_a_timezone_naive_expires_at_with_422_not_500() -> None:
    """A naive datetime parsed fine but blew up on the aware comparison."""
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token()}"}
            resp = await client.post(
                "/api/v1/models/chatgpt-subscription/device/poll",
                json={
                    "deviceAuthId": "da1",
                    "userCode": "ABCD-1234",
                    "expiresAt": "2026-08-26T12:00:00",
                },
                headers=headers,
            )
    assert resp.status_code == 422, resp.text


async def test_poll_accepts_the_frontends_javascript_iso_string() -> None:
    """The wire format `usePollChatGptDeviceLogin` actually sends:
    `new Date(...).toISOString()` -> "...Z" with milliseconds. Must keep
    working unchanged after the field became a datetime."""
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token()}"}
            js_style = (datetime.now(tz=UTC) + timedelta(minutes=10)).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"
            )
            with patch(
                "oc8.api.v1.catalog.poll_device_login",
                AsyncMock(return_value=DevicePollResult(status="pending", tokens=None, error=None)),
            ):
                resp = await client.post(
                    "/api/v1/models/chatgpt-subscription/device/poll",
                    json={
                        "deviceAuthId": "da1",
                        "userCode": "ABCD-1234",
                        "expiresAt": js_style,
                    },
                    headers=headers,
                )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"
