# backend/tests/api/test_plugins_endpoint.py
from __future__ import annotations

import base64
import uuid
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.config import get_settings
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app
from tests.conftest import AppSessionFactory

# tests/api/<this> -> tests -> backend -> repo root, where capas/ lives.
_PLUGINS_DIR = Path(__file__).resolve().parents[3] / "capas"


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )


def _token(tenant: uuid.UUID) -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")


async def test_install_and_list_plugin() -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    app = create_app()
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                "/api/v1/capas",
                json={
                    "manifest": {
                        "name": "acme.hooks",
                        "version": "1.0.0",
                        "type": "core_extension",
                        "trust": "community",
                        "permissions": ["hooks:task.before_create"],
                    }
                },
                headers=headers,
            )
            assert r.status_code == 201, r.text
            assert r.json()["type"] == "core_extension"

            lst = await client.get("/api/v1/capas", headers=headers)
            assert any(p["name"] == "acme.hooks" for p in lst.json())


async def test_declarative_setup_configures_a_tool_pack_without_vendor_code() -> None:
    tenant = uuid.uuid4()
    plugin_name = f"acme.generic-{uuid.uuid4().hex[:8]}"
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
                {"key": "token", "label": "Token", "kind": "password"},
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
                "/api/v1/capas",
                json={"manifest": manifest},
                headers=headers,
            )
            assert installed.status_code == 201, installed.text
            capa_id = installed.json()["pluginId"]
            enabled = await client.post(
                f"/api/v1/capas/{capa_id}/enable",
                json={"grantedPermissions": []},
                headers=headers,
            )
            assert enabled.status_code == 200, enabled.text

            configured = await client.post(
                f"/api/v1/capas/{capa_id}/setup",
                json={
                    "values": {
                        "url": "https://service.internal",
                        "token": "never-return-this",
                    }
                },
                headers=headers,
            )
            assert configured.status_code == 200, configured.text

            connections = await client.get("/api/v1/mcp/connections", headers=headers)
            assert connections.status_code == 200
            connection = next(
                row for row in connections.json() if row["id"] == configured.json()["connectionId"]
            )
            assert connection["name"] == "Generic service"
            assert connection["command"] == "bridge"
            assert "never-return-this" not in connections.text

            secrets = await client.get("/api/v1/secrets", headers=headers)
            assert secrets.status_code == 200
            assert any(item["name"] == f"plugin:{plugin_name}:token" for item in secrets.json())
            assert "never-return-this" not in secrets.text


async def test_setting_a_tool_pack_up_for_a_second_department_does_not_steal_the_first(
    app_session: AppSessionFactory,
) -> None:
    """One tool pack, two departments -- the normal shape once a tenant has more
    than one team using the same system.

    Setup used to MOVE the single materialised connection to whichever department
    was named last and reset it to disconnected, so configuring Odoo for support
    silently cut sales off from Odoo. Nothing failed and nothing warned; the next
    sales run just had no tools. Each department gets its own connection now.
    """
    tenant = uuid.uuid4()
    plugin_name = f"acme.two-depts-{uuid.uuid4().hex[:8]}"
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
                    "name": "Shared service",
                    "server_url": "",
                    "transport": "stdio",
                    # A manifest-only detail: it must survive onto the SECOND
                    # connection too, or the copy is subtly less capable.
                    "config": {"command": "bridge", "focus_spec": {"entity_field": "model"}},
                }
            ]
        },
        "setup": {
            "title": "Set up shared service",
            "fields": [
                {"key": "url", "label": "URL", "kind": "url"},
                {
                    "key": "department",
                    "label": "Department",
                    "kind": "department",
                    "required": False,
                },
            ],
            "mcp": {
                "connection_key": "primary",
                "name": "Shared service",
                "command": "bridge",
                "args": ["serve"],
                "scopes": ["service"],
                "env_fields": {"SERVICE_URL": "url"},
                "department_field": "department",
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

            # Created directly: there is no POST /departments yet, and this test
            # is about the connection, not about how a department is born.
            dept_ids = []
            async with app_session(tenant) as db:
                for name in ("Sales", "Support"):
                    dept = m.Department(tenant_id=tenant, name=name, frame={})
                    db.add(dept)
                    await db.flush()
                    dept_ids.append(str(dept.id))
                await db.commit()
            sales_id, support_id = dept_ids

            first = await client.post(
                f"/api/v1/capas/{capa_id}/setup",
                json={"values": {"url": "https://one.internal", "department": sales_id}},
                headers=headers,
            )
            assert first.status_code == 200, first.text
            second = await client.post(
                f"/api/v1/capas/{capa_id}/setup",
                json={"values": {"url": "https://two.internal", "department": support_id}},
                headers=headers,
            )
            assert second.status_code == 200, second.text
            assert second.json()["connectionId"] != first.json()["connectionId"]

            rows = (await client.get("/api/v1/mcp/connections", headers=headers)).json()
            by_dept = {row["departmentId"]: row for row in rows if row.get("departmentId")}
            assert sales_id in by_dept, "sales lost its connection"
            assert support_id in by_dept
            assert by_dept[sales_id]["id"] == first.json()["connectionId"]


async def test_a_setup_contract_without_mcp_stores_the_secret_and_the_plain_config(
    app_session: AppSessionFactory,
) -> None:
    """An approval_channel plugin (telegram_approvals, whatsapp_approvals in
    real life) has nothing for setup to materialise -- no McpConnection, ever.
    Its two jobs are narrower: a password field lands wherever the field itself
    names (not the plugin:{name}:{key} default other setup fields fall back to,
    because the plugin's OWN code already reads a fixed ref from its static
    [plugin.config]), and a plain field lands in PluginInstallation.config,
    which is the only thing that lets it vary by tenant at all."""
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
                    "key": "token",
                    "label": "Token",
                    "kind": "password",
                    "secret_ref": f"{plugin_name}/token",
                },
                {"key": "sender_id", "label": "Sender id", "kind": "text"},
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
                json={"values": {"token": "never-return-this", "sender_id": "12345"}},
                headers=headers,
            )
            assert configured.status_code == 200, configured.text
            assert configured.json()["connectionId"] is None

            secrets = await client.get("/api/v1/secrets", headers=headers)
            assert any(item["name"] == f"{plugin_name}/token" for item in secrets.json())
            assert "never-return-this" not in secrets.text
            # The default plugin:{name}:{key} ref must NOT also exist -- the
            # field's own secret_ref is the only place this was written.
            assert not any(item["name"] == f"plugin:{plugin_name}:token" for item in secrets.json())

            async with app_session(tenant) as db:
                installation = (
                    await db.execute(
                        select(m.CapaInstallation).where(
                            m.CapaInstallation.capa_id == uuid.UUID(capa_id)
                        )
                    )
                ).scalar_one()
                assert installation.config == {"sender_id": "12345"}


async def test_plugin_icon_404s_without_one_and_200s_for_github_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /capas/{id}/icon: a plugin whose manifest declares no `icon`
    (gitea_mcp, same as every plugin before this feature) 404s so the Capas UI
    falls back to its generic per-type icon; github_mcp, which now declares
    `icon = "icon.svg"`, serves it with the matching Content-Type."""
    monkeypatch.setenv("OC8_CAPAS_PATH", str(_PLUGINS_DIR))
    get_settings.cache_clear()
    try:
        tenant = uuid.uuid4()
        app = create_app()
        headers = {"Authorization": f"Bearer {_token(tenant)}"}
        async with LifespanManager(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
                no_icon = await client.post(
                    "/api/v1/capas/install-from-disk",
                    json={"pluginId": "gitea_mcp"},
                    headers=headers,
                )
                assert no_icon.status_code == 201, no_icon.text
                no_icon_capa_id = no_icon.json()["pluginId"]

                has_icon = await client.post(
                    "/api/v1/capas/install-from-disk",
                    json={"pluginId": "github_mcp"},
                    headers=headers,
                )
                assert has_icon.status_code == 201, has_icon.text
                has_icon_capa_id = has_icon.json()["pluginId"]

                missing = await client.get(
                    f"/api/v1/capas/{no_icon_capa_id}/icon", headers=headers
                )
                assert missing.status_code == 404

                found = await client.get(
                    f"/api/v1/capas/{has_icon_capa_id}/icon", headers=headers
                )
                assert found.status_code == 200, found.text
                assert found.headers["content-type"] == "image/svg+xml"
                assert found.text.strip().startswith("<svg")
    finally:
        get_settings.cache_clear()


async def test_setup_validate_runs_and_a_rejection_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app_session: AppSessionFactory
) -> None:
    """setup_validate is the one part of this feature that cannot be exercised
    with an in-memory manifest: it is loaded off disk, through the same trusted
    import path as a channel or connector entry point. So this test builds a
    real plugin folder -- the shape telegram_approvals/channel/setup.py actually is --
    and proves both directions: a rejected value comes back as 422 with the
    hook's own message and never reaches the secret store, and an accepted
    value is stored exactly like the test above already proved for the
    no-hook case.

    Written in the post-restructure layout: the setup form is
    `setup/fields.toml`, not an inline `[plugin.setup]` table, which discovery
    now rejects outright. The package keeps the plugin's own unique name rather
    than the generic `channel/` the real approval-channel plugins use -- a
    generic name in tmp_path would collide in `sys.modules` with whichever real
    plugin the same pytest process imported first.
    """
    plugin_name = f"acme_validated_{uuid.uuid4().hex[:8]}"
    folder = tmp_path / plugin_name
    pkg = folder / plugin_name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "setup.py").write_text(
        "async def validate(values):\n"
        "    if values.get('token') == 'reject-me':\n"
        "        raise ValueError('this token is not the one BotFather gave you')\n"
    )
    (folder / "plugin.toml").write_text(
        f"""
[plugin]
name = "{plugin_name}"
version = "1.0.0"
type = "approval_channel"
trust = "first_party"
"""
    )
    (folder / "setup").mkdir()
    (folder / "setup" / "fields.toml").write_text(
        f"""
title = "Connect the channel"
validate_entry_point = "{plugin_name}.setup:validate"

[[fields]]
key = "token"
label = "Token"
kind = "password"
"""
    )
    monkeypatch.setenv("OC8_CAPAS_PATH", str(tmp_path))
    get_settings.cache_clear()
    # cache_clear() rebuilds Settings from the environment, which drops the
    # _kek fixture's monkeypatch on the PREVIOUS instance -- reapply it to the
    # new one, or store_secret 503s before setup_validate ever runs.
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(), "secret_kek", base64.b64encode(bytes(range(32))).decode()
    )

    tenant = uuid.uuid4()
    app = create_app()
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
                installed = await client.post(
                    "/api/v1/capas/install-from-disk",
                    json={"pluginId": plugin_name},
                    headers=headers,
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

                rejected = await client.post(
                    f"/api/v1/capas/{capa_id}/setup",
                    json={"values": {"token": "reject-me"}},
                    headers=headers,
                )
                assert rejected.status_code == 422, rejected.text
                assert "BotFather" in rejected.text

                secrets = await client.get("/api/v1/secrets", headers=headers)
                assert not any(
                    item["name"] == f"plugin:{plugin_name}:token" for item in secrets.json()
                ), "a rejected setup must not leave the credential behind"

                accepted = await client.post(
                    f"/api/v1/capas/{capa_id}/setup",
                    json={"values": {"token": "a-real-token"}},
                    headers=headers,
                )
                assert accepted.status_code == 200, accepted.text
                secrets = await client.get("/api/v1/secrets", headers=headers)
                assert any(item["name"] == f"plugin:{plugin_name}:token" for item in secrets.json())
    finally:
        get_settings.cache_clear()
