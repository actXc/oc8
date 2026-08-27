from __future__ import annotations

from oc8.capas.manifest import ToolPackConnection


def test_connection_declares_a_credential_type_and_env_mapping() -> None:
    conn = ToolPackConnection.model_validate(
        {
            "key": "default",
            "name": "Odoo",
            "server_url": "stdio://odoo_mcp",
            "credential_type": "odoo_login",
            "credential_env_fields": {"ODOO_URL": "base_url", "ODOO_USER": "username"},
        }
    )
    assert conn.credential_type == "odoo_login"
    assert conn.credential_env_fields == {"ODOO_URL": "base_url", "ODOO_USER": "username"}


def test_connection_without_credential_type_defaults_empty() -> None:
    conn = ToolPackConnection.model_validate(
        {"key": "default", "name": "Legacy", "server_url": "stdio://x"}
    )
    assert conn.credential_type == ""
    assert conn.credential_env_fields == {}
