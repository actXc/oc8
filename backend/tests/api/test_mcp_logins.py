from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.capas.manifest import CredentialTypeSpec, SetupFieldSpec
from oc8.config import get_settings
from oc8.credentials import registry
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

# tests/api/<this> -> tests -> backend -> repo root, where capas/ lives --
# needed for find_plugin to resolve the real odoo_mcp manifest and its
# credential_env_fields (same convention as test_mcp_endpoint.py).
_PLUGINS_DIR = Path(__file__).resolve().parents[3] / "capas"


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    # get_key_provider reads settings.secret_kek via get_settings(); set it so
    # the store is available (mirrors tests/api/test_credentials_endpoints.py) --
    # creating a login's Credential with a password-kind field value stores it
    # in the secret vault.
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )


def _headers(tenant: uuid.UUID) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role="org_admin")
    return {"Authorization": f"Bearer {token}"}


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.fixture(autouse=True)
def _register_test_credential_type() -> Iterator[None]:
    registry.CORE_CREDENTIAL_TYPES["odoo_login"] = CredentialTypeSpec(
        name="odoo_login",
        display_name="Odoo login",
        fields=[
            SetupFieldSpec(key="base_url", label="Base URL", kind="url", required=True),
            SetupFieldSpec(key="username", label="Username", kind="text", required=True),
            SetupFieldSpec(key="password", label="Password", kind="password", required=True),
        ],
    )
    registry.CORE_CREDENTIAL_TYPES["other_login"] = CredentialTypeSpec(
        name="other_login",
        display_name="Other login",
        fields=[SetupFieldSpec(key="token", label="Token", kind="password", required=True)],
    )
    yield
    registry.CORE_CREDENTIAL_TYPES.pop("odoo_login", None)
    registry.CORE_CREDENTIAL_TYPES.pop("other_login", None)


async def test_creating_a_login_pairs_a_credential_and_a_tenant_global_connection() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            r = await c.post(
                "/api/v1/mcp/logins",
                json={
                    "name": "Odoo (User 1)",
                    "credentialType": "odoo_login",
                    "fieldValues": {
                        "base_url": "https://odoo.example.com",
                        "username": "user1",
                        "password": "secret1",
                    },
                    "scopes": {"read": ["list_customers"], "write": ["create_invoice"]},
                },
                headers=_headers(tenant),
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["name"] == "Odoo (User 1)"
            assert body["departmentId"] is None
            assert "password" not in str(body)
            assert "secret1" not in str(body)

            r = await c.get(
                "/api/v1/mcp/logins?credentialType=odoo_login", headers=_headers(tenant)
            )
            assert r.status_code == 200, r.text
            assert [row["name"] for row in r.json()] == ["Odoo (User 1)"]


async def test_creating_a_login_without_a_configured_kek_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Overrides the autouse `_kek` fixture's value for this test only (same
    # monkeypatch undo stack, mirrors tests/api/test_secrets.py's
    # test_create_secret_without_kek_returns_503) -- a password-kind field
    # routes through create_credential -> store_secret -> get_key_provider,
    # which raises SecretStoreUnavailable when secret_kek is unset.
    from oc8 import config

    monkeypatch.setattr(config.get_settings(), "secret_kek", "", raising=False)

    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            r = await c.post(
                "/api/v1/mcp/logins",
                json={
                    "name": "Odoo (User 1)",
                    "credentialType": "odoo_login",
                    "fieldValues": {
                        "base_url": "https://odoo.example.com",
                        "username": "user1",
                        "password": "secret1",
                    },
                    "scopes": {"read": ["list_customers"], "write": ["create_invoice"]},
                },
                headers=_headers(tenant),
            )
            assert r.status_code == 503, r.text


async def test_creating_a_login_reuses_an_existing_credential_instead_of_a_new_one() -> None:
    # An operator who already stored a credential -- e.g. via a capa's own
    # setup form -- should not have to retype the same values into "Create
    # login" a second time (live user report: the Odoo credential set up
    # through the capa's own setup form has no way to become a pinnable
    # login without this).
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            cred = await c.post(
                "/api/v1/credentials",
                json={
                    "name": "oc8-local",
                    "credentialType": "odoo_login",
                    "fieldValues": {
                        "base_url": "https://odoo.example.com",
                        "username": "admin",
                        "password": "secret1",
                    },
                },
                headers=_headers(tenant),
            )
            assert cred.status_code == 201, cred.text
            cred_id = cred.json()["id"]

            login = await c.post(
                "/api/v1/mcp/logins",
                json={
                    "name": "odoo",
                    "credentialType": "odoo_login",
                    "credentialId": cred_id,
                    "scopes": [],
                },
                headers=_headers(tenant),
            )
            assert login.status_code == 201, login.text
            assert login.json()["name"] == "odoo"

            # No second credential was created -- exactly the one reused.
            creds = await c.get("/api/v1/credentials?type=odoo_login", headers=_headers(tenant))
            assert [row["id"] for row in creds.json()] == [cred_id]


async def test_creating_a_login_with_an_unknown_credential_id_is_404() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            r = await c.post(
                "/api/v1/mcp/logins",
                json={
                    "name": "odoo",
                    "credentialType": "odoo_login",
                    "credentialId": str(uuid.uuid4()),
                    "scopes": [],
                },
                headers=_headers(tenant),
            )
            assert r.status_code == 404, r.text


async def test_creating_a_login_with_a_mismatched_credential_type_is_rejected() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            cred = await c.post(
                "/api/v1/credentials",
                json={
                    "name": "Some Token",
                    "credentialType": "other_login",
                    "fieldValues": {"token": "abc123"},
                },
                headers=_headers(tenant),
            )
            assert cred.status_code == 201, cred.text

            r = await c.post(
                "/api/v1/mcp/logins",
                json={
                    "name": "odoo",
                    "credentialType": "odoo_login",
                    "credentialId": cred.json()["id"],
                    "scopes": [],
                },
                headers=_headers(tenant),
            )
            assert r.status_code == 400, r.text


async def test_creating_a_second_login_with_the_same_name_is_rejected() -> None:
    # A later by-name lookup (agents_write.py's login-backed connection
    # resolution, Task 4) uses .scalar_one_or_none() filtered on
    # (tenant_id, name, credential_id IS NOT NULL) -- two logins sharing a
    # name would make that query raise MultipleResultsFound. This is
    # prevented here, at creation time, rather than defensively downstream.
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            first = await c.post(
                "/api/v1/mcp/logins",
                json={
                    "name": "Odoo Login",
                    "credentialType": "odoo_login",
                    "fieldValues": {
                        "base_url": "https://odoo.example.com",
                        "username": "user1",
                        "password": "secret1",
                    },
                    "scopes": {},
                },
                headers=_headers(tenant),
            )
            assert first.status_code == 201, first.text
            first_id = first.json()["id"]

            dup = await c.post(
                "/api/v1/mcp/logins",
                json={
                    "name": "Odoo Login",
                    "credentialType": "odoo_login",
                    "fieldValues": {
                        "base_url": "https://odoo2.example.com",
                        "username": "user2",
                        "password": "secret2",
                    },
                    "scopes": {},
                },
                headers=_headers(tenant),
            )
            assert dup.status_code == 409, dup.text

            r = await c.get(
                "/api/v1/mcp/logins?credentialType=odoo_login", headers=_headers(tenant)
            )
            assert r.status_code == 200, r.text
            rows = r.json()
            assert [row["name"] for row in rows] == ["Odoo Login"]
            assert rows[0]["id"] == first_id


async def test_creating_a_login_inherits_the_spawn_command_and_renames_env_keys(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Live bug report: a login created with no matching manifest connection's
    # command/args reached the runtime as cfg.get("command", "") -> "" ->
    # PermissionError(13, 'Permission denied') trying to spawn an empty path.
    # Uses the REAL odoo_mcp plugin on disk (its tool_pack.toml now declares
    # credential_env_fields) rather than a fake one, so this proves the wiring
    # this fix actually depends on, not just the merge logic in isolation.
    # Mutated directly on the already-cached settings singleton, like the
    # module's own `_kek` fixture -- `get_settings.cache_clear()` would
    # construct a fresh instance and lose that fixture's secret_kek mutation,
    # which runs before this test body and would otherwise 503.
    monkeypatch.setattr(get_settings(), "capas_path", str(_PLUGINS_DIR), raising=False)

    # A credential type matching odoo_login.toml's REAL field keys (url/
    # database/username/password) -- the module's own autouse fixture
    # registers a same-named but differently-shaped test double (base_url,
    # no database), which exists for the OTHER tests above and must not be
    # changed for them.
    registry.CORE_CREDENTIAL_TYPES["odoo_login"] = CredentialTypeSpec(
        name="odoo_login",
        display_name="Odoo system",
        fields=[
            SetupFieldSpec(key="url", label="URL", kind="url", required=True),
            SetupFieldSpec(key="database", label="Database", kind="text", required=True),
            SetupFieldSpec(key="username", label="Username", kind="text", required=True),
            SetupFieldSpec(key="password", label="Password", kind="password", required=True),
        ],
    )

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        # Stamped the way materialise.py stamps a real manifest connection --
        # command/args/etc. plus the PLUGIN's own demo env, which the new
        # login must inherit the shape of but NOT the values of.
        source = m.McpConnection(
            tenant_id=tenant,
            name="odoo",
            server_url="",
            transport="stdio",
            scopes=[],
            config={
                "_plugin_name": "odoo_mcp",
                "_connection_key": "primary",
                "command": "uv",
                "args": ["tool", "run", "mcp-server-odoo"],
                "env": {"ODOO_URL": "http://demo", "ODOO_DB": "demo", "ODOO_USER": "demo"},
                "secret_env": {"ODOO_PASSWORD": "demo/password"},
            },
            connected=True,
        )
        db.add(source)
        await db.flush()

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            r = await c.post(
                "/api/v1/mcp/logins",
                json={
                    "name": "odoo",
                    "credentialType": "odoo_login",
                    "fieldValues": {
                        "url": "https://custom.example.com",
                        "database": "custom_db",
                        "username": "custom_user",
                        "password": "custom_pass",
                    },
                    "scopes": [],
                },
                headers=_headers(tenant),
            )
            assert r.status_code == 201, r.text
            login_id = r.json()["id"]

    async with app_session(tenant) as db:
        conn = await db.get(m.McpConnection, uuid.UUID(login_id))
        assert conn is not None
        # Spawn shape inherited from the manifest connection.
        assert conn.config["command"] == "uv"
        assert conn.config["args"] == ["tool", "run", "mcp-server-odoo"]
        # Env keys renamed via credential_env_fields to what mcp-server-odoo
        # actually reads, carrying THIS login's own values -- not the source
        # connection's demo ones.
        assert conn.config["env"] == {
            "ODOO_URL": "https://custom.example.com",
            "ODOO_DB": "custom_db",
            "ODOO_USER": "custom_user",
        }
        # ODOO_API_KEY carries the SAME `password` field value under a second
        # env var name -- not a second secret -- so mcp-server-odoo can try
        # its API-key auth path in addition to the password path (tool_pack.
        # toml's own credential_env_fields comment explains why both names
        # are needed).
        assert set(conn.config["secret_env"]) == {"ODOO_PASSWORD", "ODOO_API_KEY"}
