# backend/tests/api/test_capa_setup_credential_field.py
from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.capas.manifest import CredentialTypeSpec, SetupFieldSpec
from oc8.credentials import registry
from oc8.credentials.service import create_credential
from oc8.main import create_app
from oc8.oauth import provisioning as oauth_provisioning
from oc8.secrets.service import resolve_secret
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
def _register_test_credential_type() -> Iterator[None]:
    registry.CORE_CREDENTIAL_TYPES["telegram_bot"] = CredentialTypeSpec(
        name="telegram_bot",
        display_name="Telegram bot",
        fields=[
            SetupFieldSpec(key="bot_token", label="Bot token", kind="password", required=False)
        ],
    )
    # odoo_mcp's actual final shape: a credential type bundling SEVERAL plain
    # fields (url, username) alongside the one password field -- the whole
    # connection profile in one reusable credential, per the user's own
    # feedback ("they belong together", then "do this for the whole
    # connection, not just the login, so I can hold several different Odoo
    # systems"). The bug this exposed along the way (2026-08-24: a stale
    # installed manifest still expecting a plain password stored the
    # submitted credential UUID itself as the password) is covered by
    # test_credential_kind_setup_field_feeds_an_mcp_connections_secret_env
    # above; the backfill tests below cover one and several bundled fields.
    registry.CORE_CREDENTIAL_TYPES["service_login"] = CredentialTypeSpec(
        name="service_login",
        display_name="Service login",
        fields=[
            SetupFieldSpec(key="url", label="URL", kind="url", required=False),
            SetupFieldSpec(key="username", label="Username", kind="text", required=False),
            SetupFieldSpec(key="password", label="Password", kind="password", required=False),
        ],
    )
    yield
    registry.CORE_CREDENTIAL_TYPES.pop("telegram_bot", None)
    registry.CORE_CREDENTIAL_TYPES.pop("service_login", None)


def _token(tenant: uuid.UUID) -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")


async def test_credential_kind_setup_field_resolves_and_stores_under_the_fixed_secret_ref(
    app_session: AppSessionFactory,
) -> None:
    """A telegram_approvals-shaped (approval_channel, no `setup.mcp`) plugin
    declares a `kind="credential"` setup field naming the `telegram_bot`
    credential_type. The browser submits a credential id, not a plaintext
    token -- configure_plugin must resolve the credential's own password-kind
    field server-side and store the RESOLVED plaintext under the setup
    field's fixed `secret_ref`, so the plugin's own runtime code (which reads
    a fixed ref via `resolve_secret`, same as the existing plain
    `kind="password"` path) needs zero changes."""
    tenant = uuid.uuid4()
    plugin_name = f"acme.channel-{uuid.uuid4().hex[:8]}"
    async with app_session(tenant) as db:
        cred = await create_credential(
            db,
            tenant_id=tenant,
            name="My bot",
            credential_type="telegram_bot",
            field_values={"bot_token": "123:ABC"},
        )
        cred_id = str(cred.id)

    app = create_app()
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    manifest = {
        "name": plugin_name,
        "version": "1.0.0",
        "type": "approval_channel",
        "trust": "first_party",
        "config": {"max_classification": "public"},
        "setup": {
            "title": "Connect the channel",
            "fields": [
                {
                    "key": "bot_token",
                    "label": "Bot token",
                    "kind": "credential",
                    "credential_type": "telegram_bot",
                    "secret_ref": "telegram/bot_token",
                },
            ],
        },
    }
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            installed = await client.post(
                "/api/v1/capas", json={"manifest": manifest}, headers=headers
            )
            assert installed.status_code == 201, installed.text
            capa_id = installed.json()["pluginId"]
            assert (
                await client.post(
                    f"/api/v1/capas/{capa_id}/enable",
                    json={"grantedPermissions": []},
                    headers=headers,
                )
            ).status_code == 200

            configured = await client.post(
                f"/api/v1/capas/{capa_id}/setup",
                json={"values": {"bot_token": cred_id}},
                headers=headers,
            )
            assert configured.status_code == 200, configured.text
            assert configured.json()["connectionId"] is None

            secrets = await client.get("/api/v1/secrets", headers=headers)
            assert secrets.status_code == 200
            assert any(item["name"] == "telegram/bot_token" for item in secrets.json())
            # The RESOLVED plaintext token must never leak back over the wire.
            assert "123:ABC" not in secrets.text

    async with app_session(tenant) as db:
        value = await resolve_secret(db, tenant_id=tenant, ref="telegram/bot_token")
        assert value == "123:ABC"


async def test_credential_kind_setup_field_feeds_an_mcp_connections_secret_env(
    app_session: AppSessionFactory,
) -> None:
    """The odoo_mcp migration's shape: an MCP-connected (`setup.mcp` present)
    manifest maps its secret through `secret_env_fields`, which used to
    require `kind == "password"` exactly and would 422 a `kind="credential"`
    field with "references non-password field" even though the secret_refs
    loop just above populates it identically for both kinds. This is the
    regression test for loosening that check."""
    tenant = uuid.uuid4()
    plugin_name = f"acme.tool-{uuid.uuid4().hex[:8]}"
    async with app_session(tenant) as db:
        cred = await create_credential(
            db,
            tenant_id=tenant,
            name="My bot",
            credential_type="telegram_bot",
            field_values={"bot_token": "s3cr3t-token"},
        )
        cred_id = str(cred.id)

    app = create_app()
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    manifest = {
        "name": plugin_name,
        "version": "1.0.0",
        "type": "tool_pack",
        "trust": "first_party",
        "tool_pack": {
            "connections": [
                {
                    "key": "primary",
                    "name": "Generic service",
                    "server_url": "",
                    "transport": "stdio",
                    "config": {"command": "bridge"},
                }
            ]
        },
        "setup": {
            "title": "Set up generic service",
            "fields": [
                {"key": "url", "label": "URL", "kind": "url"},
                {
                    "key": "token",
                    "label": "Token",
                    "kind": "credential",
                    "credential_type": "telegram_bot",
                },
            ],
            "mcp": {
                "connection_key": "primary",
                "name": "Generic service",
                "command": "bridge",
                "args": ["serve"],
                "scopes": ["service"],
                "env_fields": {"SERVICE_URL": "url"},
                "secret_env_fields": {"SERVICE_TOKEN": "token"},
            },
        },
    }
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            installed = await client.post(
                "/api/v1/capas", json={"manifest": manifest}, headers=headers
            )
            assert installed.status_code == 201, installed.text
            capa_id = installed.json()["pluginId"]
            assert (
                await client.post(
                    f"/api/v1/capas/{capa_id}/enable",
                    json={"grantedPermissions": []},
                    headers=headers,
                )
            ).status_code == 200

            configured = await client.post(
                f"/api/v1/capas/{capa_id}/setup",
                json={"values": {"url": "https://service.internal", "token": cred_id}},
                headers=headers,
            )
            assert configured.status_code == 200, configured.text
            assert configured.json()["connectionId"] is not None

            secrets = await client.get("/api/v1/secrets", headers=headers)
            assert secrets.status_code == 200
            assert any(item["name"] == f"plugin:{plugin_name}:token" for item in secrets.json())
            assert "s3cr3t-token" not in secrets.text

    async with app_session(tenant) as db:
        value = await resolve_secret(db, tenant_id=tenant, ref=f"plugin:{plugin_name}:token")
        assert value == "s3cr3t-token"


def _bundled_manifest(plugin_name: str, *, credential_required: bool) -> dict:
    """odoo_mcp's actual final shape: the ONLY connection-identifying setup
    field is the one kind="credential" field -- url/username/password all
    live in the credential_type, none of them has its own setup-form field."""
    return {
        "name": plugin_name,
        "version": "1.0.0",
        "type": "tool_pack",
        "trust": "first_party",
        "tool_pack": {
            "connections": [
                {
                    "key": "primary",
                    "name": "Generic service",
                    "server_url": "",
                    "transport": "stdio",
                    "config": {"command": "bridge"},
                }
            ]
        },
        "setup": {
            "title": "Set up generic service",
            "fields": [
                {
                    "key": "login",
                    "label": "Login",
                    "kind": "credential",
                    "credential_type": "service_login",
                    "required": credential_required,
                },
            ],
            "mcp": {
                "connection_key": "primary",
                "name": "Generic service",
                "command": "bridge",
                "args": ["serve"],
                "scopes": ["service"],
                "env_fields": {"SERVICE_URL": "url", "SERVICE_USER": "username"},
                "secret_env_fields": {"SERVICE_TOKEN": "login"},
            },
        },
    }


async def test_credential_kind_setup_field_backfills_several_bundled_plain_fields(
    app_session: AppSessionFactory,
) -> None:
    """A credential_type can declare SEVERAL plain fields (e.g. url and
    username) alongside its one password field, with no setup-form field of
    their own at all -- the user's own feedback, taken to its conclusion:
    "do this for the whole connection, not just the login, so I can hold
    several different [systems]". Each one must be resolved and written into
    `values` under the credential_type's own key, picked up by `env_fields`
    exactly as if it had always been its own plain field."""
    tenant = uuid.uuid4()
    plugin_name = f"acme.tool-{uuid.uuid4().hex[:8]}"
    async with app_session(tenant) as db:
        cred = await create_credential(
            db,
            tenant_id=tenant,
            name="Prod system",
            credential_type="service_login",
            field_values={
                "url": "https://service.internal",
                "username": "svc-user",
                "password": "s3cr3t-pw",
            },
        )
        cred_id = str(cred.id)

    app = create_app()
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    manifest = _bundled_manifest(plugin_name, credential_required=False)
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            installed = await client.post(
                "/api/v1/capas", json={"manifest": manifest}, headers=headers
            )
            assert installed.status_code == 201, installed.text
            capa_id = installed.json()["pluginId"]
            assert (
                await client.post(
                    f"/api/v1/capas/{capa_id}/enable",
                    json={"grantedPermissions": []},
                    headers=headers,
                )
            ).status_code == 200

            configured = await client.post(
                f"/api/v1/capas/{capa_id}/setup",
                json={"values": {"login": cred_id}},
                headers=headers,
            )
            assert configured.status_code == 200, configured.text
            connection_id = configured.json()["connectionId"]

    async with app_session(tenant) as db:
        connection = await db.get(m.McpConnection, uuid.UUID(connection_id))
        assert connection is not None
        assert connection.config["env"]["SERVICE_URL"] == "https://service.internal"
        assert connection.config["env"]["SERVICE_USER"] == "svc-user"
        value = await resolve_secret(db, tenant_id=tenant, ref=f"plugin:{plugin_name}:login")
        assert value == "s3cr3t-pw"


async def test_credential_kind_setup_field_backfills_bundled_plain_fields_without_a_connection(
    app_session: AppSessionFactory,
) -> None:
    """teams_approvals'/whatsapp_approvals' shape: an `approval_channel` with
    NO `setup.mcp` block, whose only credential-identifying setup field is one
    `kind="credential"` field. There is no MCP connection whose `env_fields`
    could carry the bundled plain fields, so `CapaInstallation.config` is the
    ONLY place channels/registry.py can read them back from later.

    They used to be resolved into `values`, handed to `validate()`, and then
    dropped: `_configure_without_connection` persisted only fields present in
    the SETUP FORM, and a credential_type's own ride-along fields never are.
    The channel was then rebuilt from a config missing its `app_id` and
    refused to exist at all."""
    tenant = uuid.uuid4()
    plugin_name = f"acme.channel-{uuid.uuid4().hex[:8]}"
    async with app_session(tenant) as db:
        cred = await create_credential(
            db,
            tenant_id=tenant,
            name="Prod bot",
            credential_type="service_login",
            field_values={
                "url": "https://service.internal",
                "username": "svc-user",
                "password": "s3cr3t-pw",
            },
        )
        cred_id = str(cred.id)

    app = create_app()
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    manifest = {
        "name": plugin_name,
        "version": "1.0.0",
        "type": "approval_channel",
        "trust": "first_party",
        "config": {"max_classification": "public"},
        "setup": {
            "title": "Connect the channel",
            "fields": [
                {
                    "key": "login",
                    "label": "Login",
                    "kind": "credential",
                    "credential_type": "service_login",
                    "required": True,
                    "secret_ref": "acme/bot_password",
                },
                {
                    "key": "max_classification",
                    "label": "Highest allowed confidentiality",
                    "kind": "select",
                    "options": ["public", "internal"],
                    "default": "public",
                    "required": False,
                },
            ],
        },
    }
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            installed = await client.post(
                "/api/v1/capas", json={"manifest": manifest}, headers=headers
            )
            assert installed.status_code == 201, installed.text
            capa_id = installed.json()["pluginId"]
            assert (
                await client.post(
                    f"/api/v1/capas/{capa_id}/enable",
                    json={"grantedPermissions": []},
                    headers=headers,
                )
            ).status_code == 200

            configured = await client.post(
                f"/api/v1/capas/{capa_id}/setup",
                json={"values": {"login": cred_id, "max_classification": "internal"}},
                headers=headers,
            )
            assert configured.status_code == 200, configured.text
            assert configured.json()["connectionId"] is None

    async with app_session(tenant) as db:
        installation = (
            await db.execute(
                select(m.CapaInstallation).where(
                    m.CapaInstallation.capa_id == uuid.UUID(capa_id)
                )
            )
        ).scalar_one()
        config = installation.config or {}
        assert config["url"] == "https://service.internal"
        assert config["username"] == "svc-user"
        # The form's own plain field still lands, as it always did.
        assert config["max_classification"] == "internal"
        # The credential's secret half stays in the secret store, never here.
        assert "password" not in config
        assert "s3cr3t-pw" not in str(config)
        assert await resolve_secret(db, tenant_id=tenant, ref="acme/bot_password") == "s3cr3t-pw"


async def test_credential_kind_setup_field_required_blocks_a_blank_submission_cleanly(
    app_session: AppSessionFactory,
) -> None:
    """When the credential field is the ONLY source of url/username/password
    (odoo_mcp's real shape after bundling the whole connection into it), it
    must be `required = true` -- submitting it blank must fail with the
    ordinary "field is required" 422, not fall through to env_fields'
    confusing "references unknown field 'url'" once `values` never gets url
    backfilled at all. This is a regression test for exactly that failure
    mode."""
    tenant = uuid.uuid4()
    plugin_name = f"acme.tool-{uuid.uuid4().hex[:8]}"
    app = create_app()
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    manifest = _bundled_manifest(plugin_name, credential_required=True)
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            installed = await client.post(
                "/api/v1/capas", json={"manifest": manifest}, headers=headers
            )
            assert installed.status_code == 201, installed.text
            capa_id = installed.json()["pluginId"]
            assert (
                await client.post(
                    f"/api/v1/capas/{capa_id}/enable",
                    json={"grantedPermissions": []},
                    headers=headers,
                )
            ).status_code == 200

            configured = await client.post(
                f"/api/v1/capas/{capa_id}/setup",
                json={"values": {}},
                headers=headers,
            )
            assert configured.status_code == 422, configured.text
            assert "required" in configured.text


async def test_credential_kind_setup_field_rejects_a_non_uuid_value() -> None:
    tenant = uuid.uuid4()
    plugin_name = f"acme.channel-{uuid.uuid4().hex[:8]}"
    app = create_app()
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    manifest = {
        "name": plugin_name,
        "version": "1.0.0",
        "type": "approval_channel",
        "trust": "first_party",
        "config": {"max_classification": "public"},
        "setup": {
            "title": "Connect the channel",
            "fields": [
                {
                    "key": "bot_token",
                    "label": "Bot token",
                    "kind": "credential",
                    "credential_type": "telegram_bot",
                    "secret_ref": "telegram/bot_token",
                },
            ],
        },
    }
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            installed = await client.post(
                "/api/v1/capas", json={"manifest": manifest}, headers=headers
            )
            assert installed.status_code == 201, installed.text
            capa_id = installed.json()["pluginId"]
            assert (
                await client.post(
                    f"/api/v1/capas/{capa_id}/enable",
                    json={"grantedPermissions": []},
                    headers=headers,
                )
            ).status_code == 200

            configured = await client.post(
                f"/api/v1/capas/{capa_id}/setup",
                json={"values": {"bot_token": "not-a-uuid"}},
                headers=headers,
            )
            assert configured.status_code == 422, configured.text


async def test_credential_kind_setup_field_feeds_oauth_provisioning_the_resolved_secret(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """google_workspace/microsoft365's shape: submitting the setup form ALSO
    provisions an OAuthConnection, reading the secret straight out of
    `values` SYNCHRONOUSLY in this same request (oauth/provisioning.py's
    provider-specific minting call) -- unlike the deferred, bridge-launch-time
    secret_env path every other credential-kind field only needs. Without
    overwriting `values[field.key]` with the RESOLVED secret (instead of
    leaving it as the submitted credential id), the provisioner would receive
    a credential UUID where it expects the real plaintext -- found live
    testing google_workspace/microsoft365's own migration to this pattern,
    fixed before either capa was touched."""
    tenant = uuid.uuid4()
    plugin_name = f"acme.oauth-{uuid.uuid4().hex[:8]}"
    captured: dict[str, str] = {}

    async def _fake_provision(
        db: AsyncSession, tenant_id: uuid.UUID, values: dict[str, str]
    ) -> m.OAuthConnection:
        captured.update(values)
        conn = m.OAuthConnection(
            tenant_id=tenant_id,
            provider="test_provider",
            account_label="test-account",
            access_secret_ref="test/access",
            client_source="tenant",
            grant_type="client_credentials",
            status="active",
        )
        db.add(conn)
        await db.flush()
        return conn

    monkeypatch.setitem(oauth_provisioning.PROVISIONERS, "test_provider", _fake_provision)
    monkeypatch.setitem(oauth_provisioning.PROVISIONED_FIELDS, "test_provider", ("secret",))
    registry.CORE_CREDENTIAL_TYPES["test_oauth_login"] = CredentialTypeSpec(
        name="test_oauth_login",
        display_name="Test OAuth login",
        fields=[
            SetupFieldSpec(key="client_id", label="Client ID", kind="text", required=False),
            SetupFieldSpec(key="secret", label="Secret", kind="password", required=False),
        ],
    )
    try:
        async with app_session(tenant) as db:
            cred = await create_credential(
                db,
                tenant_id=tenant,
                name="Test profile",
                credential_type="test_oauth_login",
                field_values={"client_id": "abc-123", "secret": "r34l-s3cr3t"},
            )
            cred_id = str(cred.id)

        app = create_app()
        headers = {"Authorization": f"Bearer {_token(tenant)}"}
        manifest = {
            "name": plugin_name,
            "version": "1.0.0",
            "type": "tool_pack",
            "trust": "first_party",
            "tool_pack": {
                "connections": [
                    {
                        "key": "primary",
                        "name": "Test service",
                        "server_url": "",
                        "transport": "stdio",
                        "config": {"command": "bridge"},
                    }
                ]
            },
            "setup": {
                "title": "Set up test service",
                "fields": [
                    {
                        "key": "profile",
                        "label": "Profile",
                        "kind": "credential",
                        "credential_type": "test_oauth_login",
                    },
                ],
                "mcp": {
                    "connection_key": "primary",
                    "name": "Test service",
                    "command": "bridge",
                    "args": ["serve"],
                    "scopes": ["service"],
                },
                "oauth_provision": {"provider": "test_provider"},
            },
        }
        async with LifespanManager(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
                installed = await client.post(
                    "/api/v1/capas", json={"manifest": manifest}, headers=headers
                )
                assert installed.status_code == 201, installed.text
                capa_id = installed.json()["pluginId"]
                assert (
                    await client.post(
                        f"/api/v1/capas/{capa_id}/enable",
                        json={"grantedPermissions": []},
                        headers=headers,
                    )
                ).status_code == 200

                configured = await client.post(
                    f"/api/v1/capas/{capa_id}/setup",
                    json={"values": {"profile": cred_id}},
                    headers=headers,
                )
                assert configured.status_code == 200, configured.text

        # The provisioner must have received the REAL resolved secret, not
        # the credential id the browser actually submitted.
        assert captured["secret"] == "r34l-s3cr3t"
        assert captured["client_id"] == "abc-123"

        # And the provisioned field must NOT also get a redundant, unread
        # generic secret_ref copy -- that's the whole point of
        # `provisioned_fields()`.
        async with app_session(tenant) as db:
            secret = (
                await db.execute(
                    select(m.Secret).where(m.Secret.name == f"plugin:{plugin_name}:profile")
                )
            ).scalar_one_or_none()
            assert secret is None
    finally:
        registry.CORE_CREDENTIAL_TYPES.pop("test_oauth_login", None)
