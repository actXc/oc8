"""odoo_mcp declares two `GuardrailAttribute`s (tool_pack.toml, generic
condition model dev session 2026-09-11): a numeric `order_value` (reusing
`value_spec`'s own extraction shape) and a non-numeric `model` string, added
specifically so this connection proves the generic Condition mechanism
end-to-end -- not just with the euro amount that motivated it in the first
place. These tests assemble the real plugin off disk (same path production
uses) and drive an actual tool call through `extract_attributes` +
`authorize_tool_call`, exactly as `agent/engine.py`/`api/mcp_gateway.py` do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from oc8.agent.tool_semantics import extract_attributes
from oc8.authz.pdp import Condition, Effect, ToolPolicy, authorize_tool_call, required_right
from oc8.capas.discovery import find_plugin
from oc8.capas.manifest import parse_manifest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGINS_DIR = _REPO_ROOT / "capas"
_CONNECTION_KEY = "odoo"


def _odoo_connection() -> Any:
    found = find_plugin("odoo_mcp", [str(_PLUGINS_DIR)])
    assert found is not None and found.valid, getattr(found, "error", "odoo_mcp not found")
    assert found.manifest is not None
    manifest = parse_manifest(found.manifest)
    assert manifest.tool_pack is not None
    return manifest.tool_pack.connections[0]


def _attribute_specs(conn: Any, *, tool: str) -> list[dict[str, Any]]:
    return [
        {"key": a.key, "datatype": a.datatype, "tools": a.tools, "extract": a.extract}
        for a in conn.guardrail_attributes
        if not a.tools or tool in a.tools
    ]


def test_odoo_declares_a_numeric_and_a_non_numeric_attribute() -> None:
    conn = _odoo_connection()
    by_key = {a.key: a for a in conn.guardrail_attributes}
    assert by_key["order_value"].datatype == "number"
    assert by_key["model"].datatype == "string"


def test_order_value_condition_gates_a_real_create_record_call() -> None:
    conn = _odoo_connection()
    arguments = {
        "model": "sale.order",
        "values": {
            "order_line": [
                ("create", 0, {"price_unit": 4160.0, "product_uom_qty": 3}),
            ]
        },
    }
    attributes = extract_attributes(arguments, _attribute_specs(conn, tool="create_record"))
    assert attributes["order_value"] == 12480.0
    assert attributes["model"] == "sale.order"

    policy = ToolPolicy(
        enabled=True,
        modify=True,
        conditions=(Condition(attribute="order_value", operator=">", value=5000),),
    )
    decision = authorize_tool_call(
        policies={_CONNECTION_KEY: policy},
        connection_key=_CONNECTION_KEY,
        right=required_right("create_record", conn.scopes),
        tool="create_record",
        value=None,
        attributes=attributes,
    )
    assert decision.effect is Effect.REQUIRE_APPROVAL


def test_small_order_under_the_condition_threshold_is_allowed() -> None:
    conn = _odoo_connection()
    arguments = {"model": "sale.order", "expected_revenue": 100.0}
    attributes = extract_attributes(arguments, _attribute_specs(conn, tool="create_record"))

    policy = ToolPolicy(
        enabled=True,
        modify=True,
        conditions=(Condition(attribute="order_value", operator=">", value=5000),),
    )
    decision = authorize_tool_call(
        policies={_CONNECTION_KEY: policy},
        connection_key=_CONNECTION_KEY,
        right=required_right("create_record", conn.scopes),
        tool="create_record",
        value=None,
        attributes=attributes,
    )
    assert decision.effect is Effect.ALLOW


def test_model_string_condition_blocks_deletes_on_one_model_only() -> None:
    # Proves the generic model is not secretly numeric-only: a business rule
    # entirely about WHICH Odoo model a call touches, no euro amount involved.
    conn = _odoo_connection()
    policy = ToolPolicy(
        enabled=True,
        modify=True,
        conditions=(
            Condition(attribute="model", operator="==", value="helpdesk.ticket", then=Effect.DENY),
        ),
    )

    ticket_delete = extract_attributes(
        {"model": "helpdesk.ticket", "id": 42}, _attribute_specs(conn, tool="delete_record")
    )
    decision = authorize_tool_call(
        policies={_CONNECTION_KEY: policy},
        connection_key=_CONNECTION_KEY,
        right=required_right("delete_record", conn.scopes),
        tool="delete_record",
        value=None,
        attributes=ticket_delete,
    )
    assert decision.effect is Effect.DENY

    lead_delete = extract_attributes(
        {"model": "crm.lead", "id": 7}, _attribute_specs(conn, tool="delete_record")
    )
    decision = authorize_tool_call(
        policies={_CONNECTION_KEY: policy},
        connection_key=_CONNECTION_KEY,
        right=required_right("delete_record", conn.scopes),
        tool="delete_record",
        value=None,
        attributes=lead_delete,
    )
    assert decision.effect is Effect.ALLOW
