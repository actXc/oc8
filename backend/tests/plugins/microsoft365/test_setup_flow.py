# backend/tests/plugins/microsoft365/test_setup_flow.py
from __future__ import annotations

import base64
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
from oc8.secrets.service import resolve_secret
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

_REPO_PLUGINS_DIR = Path(__file__).resolve().parents[4] / "capas"


async def _ms365_app_credential(
    app_session: AppSessionFactory,
    tenant: uuid.UUID,
    *,
    azure_tenant_id: str = "tenant-guid",
    client_id: str = "app-client-id",
    client_secret: str = "shh",
    name: str = "Test app registration",
) -> str:
    """The setup form's `app` field now bundles the whole Azure AD app
    registration into one `ms365_app` credential (2026-08-24, per the user's
    own request to generalize this beyond just odoo_mcp) -- every test below
    that used to submit azure_tenant_id/client_id/client_secret as three
    plain form values now creates a credential for them first."""
    async with app_session(tenant) as db:
        cred = await create_credential(
            db,
            tenant_id=tenant,
            name=name,
            credential_type="ms365_app",
            field_values={
                "azure_tenant_id": azure_tenant_id,
                "client_id": client_id,
                "client_secret": client_secret,
            },
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
    """Set through the ENVIRONMENT, not onto the cached Settings instance: the
    plugins-path fixture above clears that cache, and whichever of the two ran
    second would otherwise decide whether the app process sees a KEK at all."""
    from oc8.config import get_settings

    monkeypatch.setenv("OC8_SECRET_KEK", base64.b64encode(bytes(range(32))).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_installing_and_enabling_the_plugin_materialises_the_mcp_connection(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    discovered = find_plugin("microsoft365")
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
                    m.McpConnection.tenant_id == tenant, m.McpConnection.name == "microsoft365"
                )
            )
        ).scalar_one()
        assert conn.transport == "stdio"
        scopes = conn.scopes
        assert isinstance(scopes, dict)
        assert "mail_send" in scopes["send"]


async def test_submitting_the_setup_form_connects_the_tools_and_the_knowledge_base(
    app_session: AppSessionFactory,
) -> None:
    """The whole point of this plugin's setup form, end to end.

    It used to assert only that two hand-built ORM rows could reference each
    other, which is a statement about SQLAlchemy. The real submission path did
    none of this: no OAuthConnection, no DataSource, and it wiped the tool
    pack's own GRAPH_ACCESS_TOKEN mapping on every submit. Each assertion below
    is one of those failures.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "at1", "expires_in": 3600})

    oauth_http.set_transport_override(httpx.MockTransport(handler))
    tenant = uuid.uuid4()
    discovered = find_plugin("microsoft365")
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

        app_credential = await _ms365_app_credential(app_session, tenant)
        app = create_app()
        headers = {
            "Authorization": "Bearer "
            + get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")
        }
        async with LifespanManager(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
                configured = await client.post(
                    f"/api/v1/capas/{plugin_id}/setup",
                    json={
                        "values": {
                            "app": app_credential,
                            "site_ids": "site1, site2",
                        }
                    },
                    headers=headers,
                )
                assert configured.status_code == 200, configured.text
                connection_id = uuid.UUID(configured.json()["connectionId"])

        async with app_session(tenant) as db:
            oauth_conn = (
                await db.execute(
                    select(m.OAuthConnection).where(m.OAuthConnection.tenant_id == tenant)
                )
            ).scalar_one()
            assert oauth_conn.provider == "microsoft"
            assert oauth_conn.grant_type == "client_credentials"
            assert oauth_conn.azure_tenant_id == "tenant-guid"
            assert oauth_conn.account_label == "app-client-id"
            assert oauth_conn.status == "active"

            source = (
                await db.execute(
                    select(m.DataSource).where(
                        m.DataSource.tenant_id == tenant,
                        m.DataSource.connector_type == "microsoft365_files",
                    )
                )
            ).scalar_one()
            assert source.oauth_connection_id == oauth_conn.id
            assert source.config["siteIds"] == ["site1", "site2"]

            mcp_conn = await db.get(m.McpConnection, connection_id)
            assert mcp_conn is not None
            cfg = mcp_conn.config
            assert cfg["oauth_connection_id"] == str(oauth_conn.id)
            # NOT wiped: the token mapping and the bridge's import path are
            # manifest-declared, and this form manages neither.
            assert cfg["secret_env"]["GRAPH_ACCESS_TOKEN"] == "oauth:graph_access_token"
            assert cfg["env"]["PYTHONPATH"] == "/app/capas/microsoft365"
    finally:
        oauth_http.set_transport_override(None)


async def test_resubmitting_with_a_rotated_secret_updates_the_same_connection(
    app_session: AppSessionFactory,
) -> None:
    """A rotated client secret is the normal reason to open this form again.

    A second OAuthConnection row would either trip the (tenant, provider,
    account_label) uniqueness or leave the McpConnection pointing at the row
    holding the OLD secret -- so the second submit must land on the first row.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "at1", "expires_in": 3600})

    oauth_http.set_transport_override(httpx.MockTransport(handler))
    tenant = uuid.uuid4()
    discovered = find_plugin("microsoft365")
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
                for secret, sites in (("shh", "site1"), ("rotated", "site1,site3")):
                    # The picker has no "edit" affordance (only select-existing
                    # or create-new), so rotating a secret in practice means
                    # creating a NEW credential and picking it -- same
                    # azure_tenant_id/client_id, which is what
                    # `_provision_microsoft` actually keys the row on.
                    app_credential = await _ms365_app_credential(
                        app_session, tenant, client_secret=secret, name=f"App reg ({secret})"
                    )
                    r = await client.post(
                        f"/api/v1/capas/{plugin_id}/setup",
                        json={
                            "values": {
                                "app": app_credential,
                                "site_ids": sites,
                            }
                        },
                        headers=headers,
                    )
                    assert r.status_code == 200, r.text

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
            sources = (
                (await db.execute(select(m.DataSource).where(m.DataSource.tenant_id == tenant)))
                .scalars()
                .all()
            )
            assert len(sources) == 1
            assert sources[0].config["siteIds"] == ["site1", "site3"]
            stored = await resolve_secret(
                db, tenant_id=tenant, ref=str(conns[0].refresh_secret_ref)
            )
            assert stored == "rotated"
    finally:
        oauth_http.set_transport_override(None)


async def _install_and_enable(app_session: AppSessionFactory, tenant: uuid.UUID) -> uuid.UUID:
    discovered = find_plugin("microsoft365")
    assert discovered is not None and discovered.valid and discovered.manifest is not None
    async with app_session(tenant) as db:
        version = await install_plugin(db, tenant_id=tenant, manifest_data=discovered.manifest)
        await enable_plugin(
            db,
            tenant_id=tenant,
            capa_id=version.capa_id,
            granted_permissions=list(version.permissions),
        )
        return version.capa_id


async def _submit(
    plugin_id: uuid.UUID, tenant: uuid.UUID, values: dict[str, str]
) -> httpx.Response:
    app = create_app()
    headers = {
        "Authorization": "Bearer "
        + get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")
    }
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            return await client.post(
                f"/api/v1/capas/{plugin_id}/setup", json={"values": values}, headers=headers
            )


def _graph_transport(organization_status: int = 200) -> httpx.MockTransport:
    """Microsoft's token endpoint always answers; the Graph API answers with
    whatever this test wants -- which is how "the secret is valid" and "the
    permissions were admin-consented" become two different questions."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "login.microsoftonline.com" in str(request.url):
            return httpx.Response(200, json={"access_token": "at1", "expires_in": 3600})
        return httpx.Response(organization_status, json={"value": []})

    return httpx.MockTransport(handler)


async def test_emptying_the_site_ids_stops_the_knowledge_source(
    app_session: AppSessionFactory,
) -> None:
    """The field's own help text invites this -- "leave empty to skip the
    knowledge base for now" -- so emptying it must mean something.

    It meant nothing: the whole DataSource block was guarded on a non-empty
    list, so the source kept its old sites and kept syncing, and an admin had no
    way at all to turn it off from the form they turned it on with. The row is
    disconnected rather than deleted: deletion is `tombstone_source`'s job and
    takes everything the source ever ingested with it.
    """
    oauth_http.set_transport_override(_graph_transport())
    tenant = uuid.uuid4()
    try:
        plugin_id = await _install_and_enable(app_session, tenant)
        values = {
            "app": await _ms365_app_credential(app_session, tenant),
            "site_ids": "site1,site2",
        }
        assert (await _submit(plugin_id, tenant, values)).status_code == 200
        second = await _submit(plugin_id, tenant, {**values, "site_ids": ""})
        assert second.status_code == 200, second.text

        async with app_session(tenant) as db:
            source = (
                await db.execute(select(m.DataSource).where(m.DataSource.tenant_id == tenant))
            ).scalar_one()
            assert source.connected is False
            assert source.config["siteIds"] == []
            assert source.deleted_at is None, "retired, not vaporised"
    finally:
        oauth_http.set_transport_override(None)


async def test_drive_ids_alone_create_the_knowledge_source(
    app_session: AppSessionFactory,
) -> None:
    """OneDrive drives are configured through their own field, independent of
    site_ids -- a tenant with no SharePoint sites at all must still be able to
    index their OneDrive drives from this form."""
    oauth_http.set_transport_override(_graph_transport())
    tenant = uuid.uuid4()
    try:
        plugin_id = await _install_and_enable(app_session, tenant)
        answer = await _submit(
            plugin_id,
            tenant,
            {
                "app": await _ms365_app_credential(app_session, tenant),
                "drive_ids": "drive1, drive2",
            },
        )
        assert answer.status_code == 200, answer.text

        async with app_session(tenant) as db:
            source = (
                await db.execute(select(m.DataSource).where(m.DataSource.tenant_id == tenant))
            ).scalar_one()
            assert source.connected is True
            assert source.config["driveIds"] == ["drive1", "drive2"]
            assert source.config.get("siteIds") in (None, [])
    finally:
        oauth_http.set_transport_override(None)


async def test_emptying_only_the_site_ids_leaves_a_configured_drive_connected(
    app_session: AppSessionFactory,
) -> None:
    """site_ids and drive_ids are independent axes -- clearing one must not
    disconnect a source that still has ids configured on the other."""
    oauth_http.set_transport_override(_graph_transport())
    tenant = uuid.uuid4()
    try:
        plugin_id = await _install_and_enable(app_session, tenant)
        values = {
            "app": await _ms365_app_credential(app_session, tenant),
            "site_ids": "site1",
            "drive_ids": "drive1",
        }
        assert (await _submit(plugin_id, tenant, values)).status_code == 200
        second = await _submit(plugin_id, tenant, {**values, "site_ids": ""})
        assert second.status_code == 200, second.text

        async with app_session(tenant) as db:
            source = (
                await db.execute(select(m.DataSource).where(m.DataSource.tenant_id == tenant))
            ).scalar_one()
            assert source.connected is True
            assert source.config["siteIds"] == []
            assert source.config["driveIds"] == ["drive1"]
    finally:
        oauth_http.set_transport_override(None)


async def test_an_app_registration_without_graph_permissions_is_refused(
    app_session: AppSessionFactory,
) -> None:
    """A client secret that mints a token proves nothing about permissions.

    Azure issues a token for an app registration whose Graph permissions were
    never admin-consented, and every real call then 403s -- days later, on a
    schedule, with nobody watching. The connector already knows how to ask
    (`GET /organization`); the setup form was building the DataSource row by
    hand and never asking. Nothing may be left behind when it says no.
    """
    oauth_http.set_transport_override(_graph_transport(organization_status=403))
    tenant = uuid.uuid4()
    try:
        plugin_id = await _install_and_enable(app_session, tenant)
        answer = await _submit(
            plugin_id,
            tenant,
            {
                "app": await _ms365_app_credential(app_session, tenant),
                "site_ids": "site1",
            },
        )
        assert answer.status_code == 422, answer.text
        # Refused while proving the credential, before the source is even
        # considered -- `_provision_microsoft` asks Graph the same question the
        # connector's validate() asks, so for this plugin the earlier gate wins.
        # The source-level check behind it still guards everything a connector
        # refuses for reasons that are not the credential.
        assert "Graph" in answer.text

        async with app_session(tenant) as db:
            sources = (
                (await db.execute(select(m.DataSource).where(m.DataSource.tenant_id == tenant)))
                .scalars()
                .all()
            )
            assert sources == [], "no dead source"
            conns = (
                (
                    await db.execute(
                        select(m.OAuthConnection).where(m.OAuthConnection.tenant_id == tenant)
                    )
                )
                .scalars()
                .all()
            )
            assert conns == [], "and the whole submission was unwound"
    finally:
        oauth_http.set_transport_override(None)


async def test_a_tools_only_setup_still_proves_the_graph_permissions(
    app_session: AppSessionFactory,
) -> None:
    """`site_ids` is optional, and the form says so -- "leave empty to skip the
    knowledge base for now; the Mail/Calendar/Teams/Office tools still work".

    Which is precisely the submission that used to prove the least: with no
    site IDs there was no knowledge source, so the connector's Graph check never
    ran, and minting a token was the whole of the evidence. An app registration
    whose Graph permissions were never admin-consented mints one happily, so
    that admin got "connection successful" and a 403 on every tool call
    afterwards. The credential is now proven the same way whether or not the
    knowledge base is part of the setup.
    """
    oauth_http.set_transport_override(_graph_transport(organization_status=403))
    tenant = uuid.uuid4()
    try:
        plugin_id = await _install_and_enable(app_session, tenant)
        answer = await _submit(
            plugin_id,
            tenant,
            {
                "app": await _ms365_app_credential(app_session, tenant),
                "site_ids": "",
            },
        )
        assert answer.status_code == 422, answer.text
        assert "admin consent" in answer.text, "the message must name what is missing"

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
            assert conns == [], "nothing may be left claiming to be connected"
    finally:
        oauth_http.set_transport_override(None)


async def test_the_form_binds_the_connection_to_a_department_and_a_default_mailbox(
    app_session: AppSessionFactory,
) -> None:
    """Two things the form could not say at all before the whole-branch review.

    **The department (C4).** `runtime/executor.py` picks a tool connection with
    `McpConnection.department_id == agent.department_id`. `materialise.py`
    leaves that NULL, and the only thing that ever fills it is
    `setup.mcp.department_field`. Without it a tenant admin completed setup,
    got a 200, a green OAuth connection and a working token -- and every agent
    run had zero Microsoft 365 tools, with no error anywhere, because
    `NULL = <uuid>` is never true in SQL.

    **The default mailbox (C1).** This token is app-only; Graph rejects `/me`
    outright, so every Mail/Calendar/Contacts call must name a user. The bridge
    reads that from `MICROSOFT365_DEFAULT_USER`, and this is the only path that
    puts it there.
    """
    oauth_http.set_transport_override(_graph_transport())
    tenant = uuid.uuid4()
    try:
        plugin_id = await _install_and_enable(app_session, tenant)
        async with app_session(tenant) as db:
            dept = m.Department(tenant_id=tenant, name="Vertrieb")
            db.add(dept)
            await db.flush()
            department_id = dept.id
            await db.commit()

        answer = await _submit(
            plugin_id,
            tenant,
            {
                "app": await _ms365_app_credential(app_session, tenant),
                "department": str(department_id),
                "default_user": "info@contoso.com",
                "site_ids": "",
            },
        )
        assert answer.status_code == 200, answer.text

        async with app_session(tenant) as db:
            conn = await db.get(m.McpConnection, uuid.UUID(answer.json()["connectionId"]))
            assert conn is not None
            assert conn.department_id == department_id, "no agent could ever select this"
            assert conn.config["env"]["MICROSOFT365_DEFAULT_USER"] == "info@contoso.com"
            # Still not wiped by the same submit that now manages an env var.
            assert conn.config["env"]["PYTHONPATH"] == "/app/capas/microsoft365"
    finally:
        oauth_http.set_transport_override(None)


async def test_submitting_the_form_keeps_the_read_send_classification(
    app_session: AppSessionFactory,
) -> None:
    """`conn.scopes` used to be overwritten with `list(setup.mcp.scopes)` on
    every submit -- and that is `[]` for every tool pack in this repo, since no
    setup form declares scopes. That destroyed the {"read": [...], "send": [...]}
    dict `materialise.py` wrote from the manifest, after which `authz/pdp.py`
    classifies every tool as "write" (unclassified == write, fail-closed) and
    all five microsoft365 presets -- every one of which sets `write = false` --
    deny the entire pack. A successful setup submission left the
    plugin with nothing it could do."""
    oauth_http.set_transport_override(_graph_transport())
    tenant = uuid.uuid4()
    try:
        plugin_id = await _install_and_enable(app_session, tenant)
        answer = await _submit(
            plugin_id,
            tenant,
            {
                "app": await _ms365_app_credential(app_session, tenant),
                "site_ids": "",
            },
        )
        assert answer.status_code == 200, answer.text

        async with app_session(tenant) as db:
            conn = await db.get(m.McpConnection, uuid.UUID(answer.json()["connectionId"]))
            assert conn is not None
            scopes = conn.scopes
            assert isinstance(scopes, dict), f"classification wiped: {scopes!r}"
            assert "mail_send" in scopes["send"]
            assert "mail_search" in scopes["read"]
    finally:
        oauth_http.set_transport_override(None)


async def test_the_client_secret_is_stored_once_where_the_token_minter_reads_it(
    app_session: AppSessionFactory,
) -> None:
    """It used to land in two places. `configure_plugin`'s generic password loop
    wrote `plugin:microsoft365:client_secret`, and `_provision_microsoft` wrote
    the same value at `oauth/{conn.id}/refresh` -- which is the only one anything
    reads (`_mint_client_credentials`). microsoft365 declares no
    `secret_env_fields`, so the `plugin:` copy was mapped into no environment
    and read by nothing: a long-lived, high-value credential with a second home
    and no reader, which a later revocation flow clearing the OAuth side would
    leave behind entirely."""
    oauth_http.set_transport_override(_graph_transport())
    tenant = uuid.uuid4()
    try:
        plugin_id = await _install_and_enable(app_session, tenant)
        answer = await _submit(
            plugin_id,
            tenant,
            {
                "app": await _ms365_app_credential(app_session, tenant),
                "site_ids": "",
            },
        )
        assert answer.status_code == 200, answer.text

        async with app_session(tenant) as db:
            names = (
                (await db.execute(select(m.Secret.name).where(m.Secret.tenant_id == tenant)))
                .scalars()
                .all()
            )
            # Neither the old field-name-keyed ref nor the new wrapping
            # setup-field-keyed one -- the provisioned-field skip fires on
            # the credential's OWN secret key (client_secret), not the
            # wrapping field's key (app), so it's the latter this
            # regression-tests: without that correction a plugin:...:app copy
            # would appear here that nothing reads.
            assert "plugin:microsoft365:client_secret" not in names
            assert "plugin:microsoft365:app" not in names
            oauth_conn = (
                await db.execute(
                    select(m.OAuthConnection).where(m.OAuthConnection.tenant_id == tenant)
                )
            ).scalar_one()
            assert oauth_conn.refresh_secret_ref in names
            stored = await resolve_secret(
                db, tenant_id=tenant, ref=str(oauth_conn.refresh_secret_ref)
            )
            assert stored == "shh"
    finally:
        oauth_http.set_transport_override(None)


async def test_a_tools_only_setup_succeeds_once_graph_answers(
    app_session: AppSessionFactory,
) -> None:
    """The other side of the same gate: no site IDs is a legitimate setup, and
    it must still finish -- MCP connection wired, OAuth connection stored, and
    no knowledge source invented for someone who did not ask for one."""
    oauth_http.set_transport_override(_graph_transport())
    tenant = uuid.uuid4()
    try:
        plugin_id = await _install_and_enable(app_session, tenant)
        answer = await _submit(
            plugin_id,
            tenant,
            {
                "app": await _ms365_app_credential(app_session, tenant),
                "site_ids": "",
            },
        )
        assert answer.status_code == 200, answer.text

        async with app_session(tenant) as db:
            oauth_conn = (
                await db.execute(
                    select(m.OAuthConnection).where(m.OAuthConnection.tenant_id == tenant)
                )
            ).scalar_one()
            assert oauth_conn.status == "active"
            sources = (
                (await db.execute(select(m.DataSource).where(m.DataSource.tenant_id == tenant)))
                .scalars()
                .all()
            )
            assert sources == []
            mcp_conn = await db.get(m.McpConnection, uuid.UUID(answer.json()["connectionId"]))
            assert mcp_conn is not None
            assert mcp_conn.config["oauth_connection_id"] == str(oauth_conn.id)
    finally:
        oauth_http.set_transport_override(None)
