from __future__ import annotations

import base64
import uuid

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app
from oc8.oauth import http as oauth_http
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
def _transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "at1", "expires_in": 3600})

    oauth_http.set_transport_override(httpx.MockTransport(handler))
    yield
    oauth_http.set_transport_override(None)


def _token(role: str = "org_admin") -> str:
    return get_identity_provider().mint(
        tenant_id=uuid.UUID(str(ACME_TENANT_ID)), subject="admin-user", role=role
    )


async def test_creates_an_active_client_credentials_connection(
    app_session: AppSessionFactory,
) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token()}"}
            body = {
                "azureTenantId": "11111111-1111-1111-1111-111111111111",
                "clientId": "app-client-id",
                "clientSecret": "shh",
            }
            r = await client.post(
                "/api/v1/oauth/microsoft/service-connection", json=body, headers=headers
            )
            assert r.status_code == 200, r.text
            conn_id = r.json()["id"]

    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        conn = await db.get(m.OAuthConnection, uuid.UUID(conn_id))
        assert conn is not None
        assert conn.grant_type == "client_credentials"
        assert conn.azure_tenant_id == "11111111-1111-1111-1111-111111111111"
        assert conn.account_label == "app-client-id"
        assert conn.status == "active"


async def test_a_bad_secret_is_rejected_at_creation(app_session: AppSessionFactory) -> None:
    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client"})

    oauth_http.set_transport_override(httpx.MockTransport(failing_handler))
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token()}"}
            body = {
                "azureTenantId": "11111111-1111-1111-1111-111111111111",
                # A distinct client_id from the other test in this module: both
                # share ACME_TENANT_ID (see app_session's tenant binding) and
                # OAuthConnection is unique on (tenant_id, provider,
                # account_label) -- reusing "app-client-id" here would collide
                # with the row the first test committed, independent of this
                # test's own bad-secret rejection.
                "clientId": "app-client-id-bad-secret",
                "clientSecret": "wrong",
            }
            r = await client.post(
                "/api/v1/oauth/microsoft/service-connection", json=body, headers=headers
            )
            assert r.status_code == 400


async def test_requires_integration_manage_permission() -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(role='member')}"}
            body = {"azureTenantId": "x", "clientId": "y", "clientSecret": "z"}
            r = await client.post(
                "/api/v1/oauth/microsoft/service-connection", json=body, headers=headers
            )
            assert r.status_code == 403
