from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import Select, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.oauth import http as oauth_http
from oc8.oauth.tokens import access_ref, get_access_token, refresh_ref
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


class _Recorder:
    def __init__(self, body: dict[str, object]) -> None:
        self.body = body
        self.calls: list[dict[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json=self.body)


@pytest.fixture
def _transport() -> Iterator[None]:
    yield
    oauth_http.set_transport_override(None)


async def _make_client_credentials_connection(
    db: AsyncSession, tenant: uuid.UUID, *, expires_at: datetime
) -> m.OAuthConnection:
    cid = uuid.uuid4()
    conn = m.OAuthConnection(
        id=cid,
        tenant_id=tenant,
        provider="microsoft",
        account_label="contoso",
        access_secret_ref=access_ref(cid),
        refresh_secret_ref=refresh_ref(cid),  # holds the client secret, not a refresh token
        expires_at=expires_at,
        status="active",
        client_source="tenant",
        grant_type="client_credentials",
        azure_tenant_id="tenant-guid",
    )
    db.add(conn)
    await db.flush()
    await store_secret(db, tenant_id=tenant, name=access_ref(cid), value="at0", kind="oauth_token")
    await store_secret(
        db, tenant_id=tenant, name=refresh_ref(cid), value="the-client-secret", kind="oauth_token"
    )
    await db.flush()
    return conn


async def test_mints_a_fresh_token_when_expired(
    app_session: AppSessionFactory, _transport: None
) -> None:
    rec = _Recorder({"access_token": "at1", "expires_in": 3600})
    oauth_http.set_transport_override(httpx.MockTransport(rec.handler))
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        conn = await _make_client_credentials_connection(
            db, tenant, expires_at=datetime.now(tz=UTC) - timedelta(seconds=1)
        )
        token = await get_access_token(db, tenant_id=tenant, connection_id=conn.id)
    assert token == "at1"
    assert len(rec.calls) == 1
    assert rec.calls[0]["grant_type"] == "client_credentials"
    assert rec.calls[0]["client_secret"] == "the-client-secret"
    assert rec.calls[0]["scope"] == "https://graph.microsoft.com/.default"


class _UrlRecorder(_Recorder):
    def __init__(self, body: dict[str, object]) -> None:
        super().__init__(body)
        self.urls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        return super().handler(request)


async def test_uses_the_tenant_scoped_token_url(
    app_session: AppSessionFactory, _transport: None
) -> None:
    rec = _UrlRecorder({"access_token": "at1", "expires_in": 3600})
    oauth_http.set_transport_override(httpx.MockTransport(rec.handler))
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        conn = await _make_client_credentials_connection(
            db, tenant, expires_at=datetime.now(tz=UTC) - timedelta(seconds=1)
        )
        await get_access_token(db, tenant_id=tenant, connection_id=conn.id)
    assert rec.urls == ["https://login.microsoftonline.com/tenant-guid/oauth2/v2.0/token"]


async def test_still_fresh_token_is_returned_without_a_new_mint(
    app_session: AppSessionFactory, _transport: None
) -> None:
    rec = _Recorder({"access_token": "should-not-be-used", "expires_in": 3600})
    oauth_http.set_transport_override(httpx.MockTransport(rec.handler))
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        conn = await _make_client_credentials_connection(
            db, tenant, expires_at=datetime.now(tz=UTC) + timedelta(hours=1)
        )
        token = await get_access_token(db, tenant_id=tenant, connection_id=conn.id)
    assert token == "at0"
    assert rec.calls == []


def _lock_nowait(connection_id: uuid.UUID) -> Select[tuple[m.OAuthConnection]]:
    return (
        select(m.OAuthConnection)
        .where(m.OAuthConnection.id == connection_id)
        .with_for_update(nowait=True)
    )


async def test_handing_back_a_fresh_token_locks_nothing(
    app_session: AppSessionFactory, _transport: None
) -> None:
    """The read path must not serialise on the connection row.

    It used to: the `FOR UPDATE` was taken before anything had looked at the
    expiry, which was cheap while the only caller was a nightly sync and stopped
    being cheap the moment every gateway tool call resolved a token through
    here. Two agents on one Microsoft 365 connection then queued behind each
    other for a whole far-system round trip, to discover that nothing needed
    doing. `NOWAIT` from a second transaction is the proof: it raises rather
    than waits, so it fails if the first transaction holds the lock.
    """
    rec = _Recorder({"access_token": "should-not-be-used", "expires_in": 3600})
    oauth_http.set_transport_override(httpx.MockTransport(rec.handler))
    tenant = uuid.uuid4()
    async with app_session(tenant) as setup:
        conn_id = (
            await _make_client_credentials_connection(
                setup, tenant, expires_at=datetime.now(tz=UTC) + timedelta(hours=1)
            )
        ).id

    async with app_session(tenant) as reader:
        assert await get_access_token(reader, tenant_id=tenant, connection_id=conn_id) == "at0"
        # Still inside the reader's transaction.
        async with app_session(tenant) as other:
            assert (await other.execute(_lock_nowait(conn_id))).scalar_one().id == conn_id


async def test_the_re_mint_path_still_takes_the_lock(
    app_session: AppSessionFactory, _transport: None
) -> None:
    """The other half of the same change: the lock is not gone, it is deferred
    to the path that writes. Two concurrent re-mints on one connection must not
    both run -- one of them would persist a token the other has replaced."""
    rec = _Recorder({"access_token": "at1", "expires_in": 3600})
    oauth_http.set_transport_override(httpx.MockTransport(rec.handler))
    tenant = uuid.uuid4()
    async with app_session(tenant) as setup:
        conn_id = (
            await _make_client_credentials_connection(
                setup, tenant, expires_at=datetime.now(tz=UTC) - timedelta(seconds=1)
            )
        ).id

    async with app_session(tenant) as minter:
        assert await get_access_token(minter, tenant_id=tenant, connection_id=conn_id) == "at1"
        with pytest.raises(DBAPIError):
            async with app_session(tenant) as other:
                await other.execute(_lock_nowait(conn_id))
