from __future__ import annotations

import base64
import json
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
from tests.oauth._keys import TEST_PRIVATE_KEY_PEM

pytestmark = pytest.mark.asyncio

_KEY_JSON = json.dumps(
    {
        "type": "service_account",
        "client_email": "svc@p.iam.gserviceaccount.com",
        "private_key": TEST_PRIVATE_KEY_PEM,
    }
)


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
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


def _handler(request: httpx.Request) -> httpx.Response:
    if "token" in str(request.url):
        return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
    return httpx.Response(200, json={"id": "d1"})


async def test_service_connection_endpoint_creates_a_google_connection(
    app_session: AppSessionFactory,
) -> None:
    oauth_http.set_transport_override(httpx.MockTransport(_handler))
    try:
        app = create_app()
        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://t") as client:
                headers = {"Authorization": f"Bearer {_token()}"}
                body = {
                    "serviceAccountKeyJson": _KEY_JSON,
                    "sharedDriveIds": "d1",
                    "delegatedMailboxes": "",
                }
                r = await client.post(
                    "/api/v1/oauth/google/service-connection", json=body, headers=headers
                )
                assert r.status_code == 200, r.text
                body_resp = r.json()
                assert body_resp["provider"] == "google"
                conn_id = body_resp["id"]

        tenant = uuid.UUID(str(ACME_TENANT_ID))
        async with app_session(tenant) as db:
            conn = await db.get(m.OAuthConnection, uuid.UUID(conn_id))
            assert conn is not None
            assert conn.grant_type == "service_account"
            assert conn.account_label == "svc@p.iam.gserviceaccount.com"
    finally:
        oauth_http.set_transport_override(None)


async def test_a_bad_service_account_key_is_rejected_at_creation() -> None:
    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    oauth_http.set_transport_override(httpx.MockTransport(failing_handler))
    try:
        app = create_app()
        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://t") as client:
                headers = {"Authorization": f"Bearer {_token()}"}
                key_json = json.dumps(
                    {
                        "type": "service_account",
                        "client_email": "bad@p.iam.gserviceaccount.com",
                        "private_key": TEST_PRIVATE_KEY_PEM,
                    }
                )
                body = {
                    "serviceAccountKeyJson": key_json,
                    "sharedDriveIds": "d1",
                    "delegatedMailboxes": "",
                }
                r = await client.post(
                    "/api/v1/oauth/google/service-connection", json=body, headers=headers
                )
                assert r.status_code == 400
    finally:
        oauth_http.set_transport_override(None)


async def test_requires_integration_manage_permission() -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(role='member')}"}
            body = {
                "serviceAccountKeyJson": _KEY_JSON,
                "sharedDriveIds": "",
                "delegatedMailboxes": "",
            }
            r = await client.post(
                "/api/v1/oauth/google/service-connection", json=body, headers=headers
            )
            assert r.status_code == 403


async def test_service_connection_endpoint_uses_delegated_probe_when_no_shared_drive(
    app_session: AppSessionFactory,
) -> None:
    """Unconditional-probe case 2: no Shared Drive configured, a delegated
    mailbox is -- the endpoint must fall back to a delegated Gmail profile
    read rather than skip the proof entirely."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "token" in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if "gmail.googleapis.com" in str(request.url):
            return httpx.Response(200, json={"emailAddress": "person@example.com"})
        raise AssertionError(f"unexpected request: {request.url}")

    oauth_http.set_transport_override(httpx.MockTransport(handler))
    try:
        app = create_app()
        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://t") as client:
                headers = {"Authorization": f"Bearer {_token()}"}
                key_json = json.dumps(
                    {
                        "type": "service_account",
                        "client_email": "delegated@p.iam.gserviceaccount.com",
                        "private_key": TEST_PRIVATE_KEY_PEM,
                    }
                )
                body = {
                    "serviceAccountKeyJson": key_json,
                    "sharedDriveIds": "",
                    "delegatedMailboxes": "person@example.com",
                }
                r = await client.post(
                    "/api/v1/oauth/google/service-connection", json=body, headers=headers
                )
                assert r.status_code == 200, r.text
                assert r.json()["provider"] == "google"
    finally:
        oauth_http.set_transport_override(None)
