# backend/tests/plugins/google_workspace/test_setup_flow.py
from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.capas.discovery import find_plugin
from oc8.capas.lifecycle import enable_plugin
from oc8.capas.service import install_plugin
from oc8.credentials.service import create_credential
from oc8.main import create_app
from oc8.oauth import http as oauth_http
from tests.conftest import AppSessionFactory
from tests.oauth._keys import TEST_PRIVATE_KEY_PEM

pytestmark = pytest.mark.asyncio

_REPO_PLUGINS_DIR = Path(__file__).resolve().parents[4] / "capas"

_KEY_JSON = json.dumps(
    {
        "type": "service_account",
        "client_email": "svc@p.iam.gserviceaccount.com",
        "private_key": TEST_PRIVATE_KEY_PEM,
    }
)


async def _google_service_account_credential(
    app_session: AppSessionFactory,
    tenant: uuid.UUID,
    *,
    key_json: str = _KEY_JSON,
    name: str = "Test service account",
) -> str:
    """The setup form's `service_account_key` field now resolves through the
    shared credentials store (2026-08-24, per the user's own request to
    generalize this beyond just odoo_mcp) -- every test below that used to
    submit the raw key JSON as a plain form value now creates a credential
    for it first."""
    async with app_session(tenant) as db:
        cred = await create_credential(
            db,
            tenant_id=tenant,
            name=name,
            credential_type="google_service_account",
            field_values={"service_account_key": key_json},
        )
        return str(cred.id)


@pytest.fixture(autouse=True)
def _real_plugins_path(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from oc8.config import get_settings

    monkeypatch.setenv("OC8_CAPAS_PATH", str(_REPO_PLUGINS_DIR))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch, _real_plugins_path: None) -> Iterator[None]:
    from oc8.config import get_settings

    monkeypatch.setenv("OC8_SECRET_KEK", base64.b64encode(bytes(range(32))).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _token_and_drive_handler(request: httpx.Request) -> httpx.Response:
    if "oauth2.googleapis.com/token" in str(request.url):
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    if "drive/v3/drives/" in str(request.url):
        return httpx.Response(200, json={"id": "drive-1"})
    if "gmail.googleapis.com" in str(request.url):
        return httpx.Response(200, json={"emailAddress": "agents@company.com"})
    raise AssertionError(f"unexpected request: {request.url}")


def _drive_ok_gmail_forbidden_handler(request: httpx.Request) -> httpx.Response:
    """Same as `_token_and_drive_handler` except domain-wide delegation was
    never actually authorized for the submitted mailbox: the Drive probe
    succeeds (valid service account, Shared Drive membership correct) but the
    delegated Gmail probe comes back 403, which is exactly what a tenant who
    filled in the Shared Drive fields correctly but never authorized the
    Gmail/Calendar scopes in the Admin Console would see."""
    if "oauth2.googleapis.com/token" in str(request.url):
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    if "drive/v3/drives/" in str(request.url):
        return httpx.Response(200, json={"id": "drive-1"})
    if "gmail.googleapis.com" in str(request.url):
        return httpx.Response(403, json={"error": "unauthorized_client"})
    raise AssertionError(f"unexpected request: {request.url}")


def test_delegated_scope_matches_the_provisioning_probe_byte_for_byte() -> None:
    """Three places must agree on this exact scope string: the manifest's
    `delegated_scope` (what every launch mints with), `oauth/provisioning.py`'s
    `_DELEGATED_SCOPE` (what the setup-time consent probe requests), and
    docs/GOOGLE_WORKSPACE.md (what an admin is told to authorize in the
    Workspace Admin Console). Google's delegation check is scope-exact, so a
    silent drift between the first two would make a correctly-configured
    tenant's setup probe pass with one scope while every real launch mints
    with a different one -- this test makes that constraint enforceable
    instead of resting on three separate code comments."""
    from oc8.oauth.provisioning import _DELEGATED_SCOPE

    discovered = find_plugin("google_workspace")
    assert discovered is not None and discovered.valid and discovered.manifest is not None
    manifest_scope = discovered.manifest["setup"]["oauth_provision"]["delegated_scope"]
    assert manifest_scope == _DELEGATED_SCOPE


async def test_installing_and_enabling_the_plugin_materialises_the_mcp_connection(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    discovered = find_plugin("google_workspace")
    assert discovered is not None and discovered.valid, (
        discovered.error if discovered else "not found"
    )
    assert discovered.manifest is not None
    async with app_session(tenant) as db:
        version = await install_plugin(db, tenant_id=tenant, manifest_data=discovered.manifest)
        await enable_plugin(
            db,
            tenant_id=tenant,
            capa_id=version.capa_id,
            granted_permissions=list(version.permissions),
        )
        conn = (
            await db.execute(
                select(m.McpConnection).where(
                    m.McpConnection.tenant_id == tenant, m.McpConnection.name == "google_workspace"
                )
            )
        ).scalar_one()
        assert conn.transport == "stdio"
        assert "gmail_send" in conn.scopes["modify"]


async def _submit_setup(
    tenant: uuid.UUID, plugin_id: uuid.UUID, values: dict[str, str]
) -> uuid.UUID:
    app = create_app()
    headers = {
        "Authorization": "Bearer "
        + get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")
    }
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(
                f"/api/v1/capas/{plugin_id}/setup", json={"values": values}, headers=headers
            )
            assert resp.status_code == 200, resp.text
            return uuid.UUID(resp.json()["connectionId"])


async def test_submitting_the_form_with_shared_drives_and_delegated_mailboxes(
    app_session: AppSessionFactory,
) -> None:
    oauth_http.set_transport_override(httpx.MockTransport(_token_and_drive_handler))
    tenant = uuid.uuid4()
    discovered = find_plugin("google_workspace")
    assert discovered is not None and discovered.valid and discovered.manifest is not None
    try:
        async with app_session(tenant) as db:
            version = await install_plugin(db, tenant_id=tenant, manifest_data=discovered.manifest)
            plugin_id = version.capa_id
            await enable_plugin(
                db,
                tenant_id=tenant,
                capa_id=plugin_id,
                granted_permissions=list(version.permissions),
            )

        connection_id = await _submit_setup(
            tenant,
            plugin_id,
            {
                "service_account_key": await _google_service_account_credential(
                    app_session, tenant
                ),
                "shared_drive_ids": "drive-1",
                "delegated_mailboxes": "agents@company.com",
                "default_mailbox": "agents@company.com",
            },
        )

        async with app_session(tenant) as db:
            oauth_conn = (
                await db.execute(
                    select(m.OAuthConnection).where(m.OAuthConnection.tenant_id == tenant)
                )
            ).scalar_one()
            assert oauth_conn.grant_type == "service_account"
            assert oauth_conn.account_label == "svc@p.iam.gserviceaccount.com"

            data_source = (
                await db.execute(
                    select(m.DataSource).where(
                        m.DataSource.tenant_id == tenant,
                        m.DataSource.connector_type == "google_workspace_files",
                    )
                )
            ).scalar_one()
            assert data_source.oauth_connection_id == oauth_conn.id
            assert data_source.config["sharedDriveIds"] == ["drive-1"]

            mcp_conn = (
                await db.execute(select(m.McpConnection).where(m.McpConnection.id == connection_id))
            ).scalar_one()
            assert mcp_conn.config["oauth_connection_id"] == str(oauth_conn.id)
            assert mcp_conn.config["env"]["GOOGLE_DELEGATED_MAILBOXES"] == "agents@company.com"
            assert (
                mcp_conn.config["secret_env"]["GOOGLE_DELEGATED_TOKEN_0"]
                == "oauth-delegated:agents@company.com"
            )
            assert mcp_conn.config["secret_env"]["GOOGLE_TOKEN"] == "oauth:google_token"
            assert mcp_conn.config["env"]["PYTHONPATH"] == "/app/capas/google_workspace"
            assert mcp_conn.config["env"]["GOOGLE_DEFAULT_MAILBOX"] == "agents@company.com"
            assert mcp_conn.department_id is None
    finally:
        oauth_http.set_transport_override(None)


async def test_submitting_shared_drives_and_delegated_mailboxes_together_still_proves_delegation(
    app_session: AppSessionFactory,
) -> None:
    """Task 12 review Important finding: with both `shared_drive_ids` and
    `delegated_mailboxes` submitted together (a normal, expected combined
    configuration -- likely the most common one, since most tenants want both
    knowledge-base access and Gmail/Calendar tools), a Drive probe that
    succeeds must NOT stand in as proof that domain-wide delegation also
    works. Mutation-testing the flagship
    `test_submitting_the_form_with_shared_drives_and_delegated_mailboxes` test
    above (deleting its mock's Gmail branch) left it passing, meaning nothing
    exercised the "both configured, Gmail probe actually gets called" path.
    This drives a real 403 through the Gmail probe with a passing Drive probe
    and asserts the whole setup call fails clearly, through the real
    `/capas/{id}/setup` endpoint an admin actually uses -- not a narrow unit
    test of `_prove_google_access` in isolation."""
    oauth_http.set_transport_override(httpx.MockTransport(_drive_ok_gmail_forbidden_handler))
    tenant = uuid.uuid4()
    discovered = find_plugin("google_workspace")
    assert discovered is not None and discovered.valid and discovered.manifest is not None
    try:
        async with app_session(tenant) as db:
            version = await install_plugin(db, tenant_id=tenant, manifest_data=discovered.manifest)
            plugin_id = version.capa_id
            await enable_plugin(
                db,
                tenant_id=tenant,
                capa_id=plugin_id,
                granted_permissions=list(version.permissions),
            )

        app = create_app()
        headers = {
            "Authorization": "Bearer "
            + get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")
        }
        async with LifespanManager(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
                resp = await client.post(
                    f"/api/v1/capas/{plugin_id}/setup",
                    json={
                        "values": {
                            "service_account_key": await _google_service_account_credential(
                                app_session, tenant
                            ),
                            "shared_drive_ids": "drive-1",
                            "delegated_mailboxes": "agents@company.com",
                            "default_mailbox": "agents@company.com",
                        }
                    },
                    headers=headers,
                )
                assert resp.status_code == 422, resp.text
                assert "domain-wide delegation" in resp.text

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
            assert conns == [], (
                "a setup call that failed to prove delegation must not leave a "
                "green-looking OAuth connection behind"
            )
    finally:
        oauth_http.set_transport_override(None)


async def test_the_form_binds_the_connection_to_a_department(
    app_session: AppSessionFactory,
) -> None:
    """`runtime/executor.py` picks a tool connection with
    `McpConnection.department_id == agent.department_id`. Without the
    `department_field` mapping, that column stays NULL and no agent can ever
    select this connection -- the Microsoft 365 plugin's whole-branch review
    finding C4, applied here so it never has a chance to recur."""
    oauth_http.set_transport_override(httpx.MockTransport(_token_and_drive_handler))
    tenant = uuid.uuid4()
    discovered = find_plugin("google_workspace")
    assert discovered is not None and discovered.valid and discovered.manifest is not None
    try:
        async with app_session(tenant) as db:
            version = await install_plugin(db, tenant_id=tenant, manifest_data=discovered.manifest)
            plugin_id = version.capa_id
            await enable_plugin(
                db,
                tenant_id=tenant,
                capa_id=plugin_id,
                granted_permissions=list(version.permissions),
            )
            dept = m.Department(tenant_id=tenant, name="Vertrieb")
            db.add(dept)
            await db.flush()
            department_id = dept.id
            await db.commit()

        connection_id = await _submit_setup(
            tenant,
            plugin_id,
            {
                "service_account_key": await _google_service_account_credential(
                    app_session, tenant
                ),
                "shared_drive_ids": "drive-1",
                "department": str(department_id),
            },
        )

        async with app_session(tenant) as db:
            conn = await db.get(m.McpConnection, connection_id)
            assert conn is not None
            assert conn.department_id == department_id, "no agent could ever select this"
    finally:
        oauth_http.set_transport_override(None)


async def test_resubmitting_with_fewer_delegated_mailboxes_drops_the_removed_ones(
    app_session: AppSessionFactory,
) -> None:
    """Regression for the stale-value class of bug the Microsoft plugin's
    env/secret_env merge was audited against (whole-branch review, Important
    #4) -- applied here to the NEW delegated-identity mechanism rather than
    assuming it inherits that discipline for free."""
    oauth_http.set_transport_override(httpx.MockTransport(_token_and_drive_handler))
    tenant = uuid.uuid4()
    discovered = find_plugin("google_workspace")
    assert discovered is not None and discovered.valid and discovered.manifest is not None
    try:
        async with app_session(tenant) as db:
            version = await install_plugin(db, tenant_id=tenant, manifest_data=discovered.manifest)
            plugin_id = version.capa_id
            await enable_plugin(
                db,
                tenant_id=tenant,
                capa_id=plugin_id,
                granted_permissions=list(version.permissions),
            )

        credential = await _google_service_account_credential(app_session, tenant)
        connection_id = await _submit_setup(
            tenant,
            plugin_id,
            {
                "service_account_key": credential,
                "shared_drive_ids": "",
                "delegated_mailboxes": "a@company.com, b@company.com",
            },
        )
        await _submit_setup(
            tenant,
            plugin_id,
            {
                "service_account_key": credential,
                "shared_drive_ids": "",
                "delegated_mailboxes": "a@company.com",
            },
        )

        async with app_session(tenant) as db:
            mcp_conn = (
                await db.execute(select(m.McpConnection).where(m.McpConnection.id == connection_id))
            ).scalar_one()
            assert mcp_conn.config["env"]["GOOGLE_DELEGATED_MAILBOXES"] == "a@company.com"
            assert (
                mcp_conn.config["secret_env"]["GOOGLE_DELEGATED_TOKEN_0"]
                == "oauth-delegated:a@company.com"
            )
            assert "GOOGLE_DELEGATED_TOKEN_1" not in mcp_conn.config["secret_env"]
    finally:
        oauth_http.set_transport_override(None)


async def test_setup_rejects_a_both_empty_submission(app_session: AppSessionFactory) -> None:
    """Neither Shared Drives nor delegated mailboxes configured proves
    nothing was authorized yet -- the setup form must reject this, not
    silently succeed with 31 tools that all 403/404."""
    tenant = uuid.uuid4()
    discovered = find_plugin("google_workspace")
    assert discovered is not None and discovered.valid and discovered.manifest is not None
    async with app_session(tenant) as db:
        version = await install_plugin(db, tenant_id=tenant, manifest_data=discovered.manifest)
        plugin_id = version.capa_id
        await enable_plugin(
            db, tenant_id=tenant, capa_id=plugin_id, granted_permissions=list(version.permissions)
        )

    app = create_app()
    headers = {
        "Authorization": "Bearer "
        + get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")
    }
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(
                f"/api/v1/capas/{plugin_id}/setup",
                json={
                    "values": {
                        "service_account_key": await _google_service_account_credential(
                            app_session, tenant, key_json="{}"
                        ),
                        "shared_drive_ids": "",
                        "delegated_mailboxes": "",
                    }
                },
                headers=headers,
            )
            assert resp.status_code == 422
            assert "shared_drive_ids" in resp.text or "delegated_mailboxes" in resp.text

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
        assert conns == [], "a rejected submission left no dangling OAuth connection"


async def test_setup_rejects_a_default_mailbox_not_in_the_delegated_list(
    app_session: AppSessionFactory,
) -> None:
    """A typo'd `default_mailbox` must 422 at setup time, not surface as a
    confusing runtime error deep inside a later agent run
    (`google_api.py`'s `_resolve_mailbox`, minutes or days later, with no
    reference back to the setup form). `_provision_google`
    (`oauth/provisioning.py`) already checks membership before minting
    anything; this proves that check is actually reachable end to end through
    the setup endpoint, not just unit-tested against the provisioner in
    isolation."""
    oauth_http.set_transport_override(httpx.MockTransport(_token_and_drive_handler))
    tenant = uuid.uuid4()
    discovered = find_plugin("google_workspace")
    assert discovered is not None and discovered.valid and discovered.manifest is not None
    try:
        async with app_session(tenant) as db:
            version = await install_plugin(db, tenant_id=tenant, manifest_data=discovered.manifest)
            plugin_id = version.capa_id
            await enable_plugin(
                db,
                tenant_id=tenant,
                capa_id=plugin_id,
                granted_permissions=list(version.permissions),
            )

        app = create_app()
        headers = {
            "Authorization": "Bearer "
            + get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")
        }
        async with LifespanManager(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
                resp = await client.post(
                    f"/api/v1/capas/{plugin_id}/setup",
                    json={
                        "values": {
                            "service_account_key": await _google_service_account_credential(
                                app_session, tenant
                            ),
                            "delegated_mailboxes": "agents@company.com",
                            "default_mailbox": "typo@company.com",
                        }
                    },
                    headers=headers,
                )
                assert resp.status_code == 422, resp.text
                assert "default_mailbox" in resp.text

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
            assert conns == [], "the whole submission was unwound, not left half-authorized"
    finally:
        oauth_http.set_transport_override(None)
