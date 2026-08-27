"""_refresh_declared_seams carries a manifest's own DECLARED_SEAMS config
keys onto a connection already installed for a tenant -- without it, a
connection is frozen at whatever config existed when it was first
materialised, and a later manifest edit (a new focus_spec entity, a new
tool_note) silently never reaches an existing tenant. tool_notes joined
this set 2026-08-26 after exactly that gap: an odoo_mcp connection
installed before tool_notes existed had no way to pick it up short of a
direct DB patch (live-observed) -- see oc8.agent.tool_notes."""

from __future__ import annotations

import uuid

from oc8 import models as m
from oc8.capas.manifest import ToolPackConnection
from oc8.capas.materialise import DECLARED_SEAMS, _refresh_declared_seams


def _connection(**config: object) -> m.McpConnection:
    return m.McpConnection(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="odoo",
        server_url="",
        transport="stdio",
        scopes={"read": ["search_records"]},
        config=dict(config),
        connected=False,
        health={},
    )


def test_tool_notes_is_a_declared_seam() -> None:
    assert "tool_notes" in DECLARED_SEAMS


def test_a_new_tool_notes_entry_reaches_an_already_installed_connection() -> None:
    existing = _connection(command="uv", args=["tool", "run", "mcp-server-odoo"])
    declared = ToolPackConnection(
        name="odoo",
        server_url="",
        config={"tool_notes": {"search_records": "helpdesk.ticket has no sla_date field."}},
    )

    _refresh_declared_seams(existing, declared)

    assert existing.config["tool_notes"] == {
        "search_records": "helpdesk.ticket has no sla_date field."
    }
    # Untouched: refresh must not drop config the connection already had.
    assert existing.config["command"] == "uv"


def test_an_unchanged_tool_notes_value_does_not_touch_the_connection() -> None:
    existing = _connection(tool_notes={"search_records": "same note"})
    declared = ToolPackConnection(
        name="odoo", server_url="", config={"tool_notes": {"search_records": "same note"}}
    )
    before = existing.config

    _refresh_declared_seams(existing, declared)

    assert existing.config is before, "identical declared value must not reassign config"
