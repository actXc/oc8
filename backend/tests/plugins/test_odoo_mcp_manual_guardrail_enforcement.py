"""The frontend's manual per-function editor (`guardrail-function-rules.tsx`)
writes exactly the same `only`/`approvalActions`/`read`/`modify` fields as a
predefined guardrail does -- `test_odoo_mcp_guardrails.py` already proves the
library/preset half of that shape decides correctly at the real PDP gate.
This file proves the OTHER half: hand-authored rows (a user ticking one
function's checkbox in the table, not applying a preset) decide the same way,
against odoo_mcp's real manifest scopes -- so the checkboxes fixed in that
component (dev session 2026-09-10) are not just cosmetically consistent, they
enforce.

No browser involved: `authorize_tool_call` is the exact function
`agent/engine.py` and `api/mcp_gateway.py` call for every real tool call
(see pdp.py's own module docstring), and `required_right` is the exact
classification both call sites use to turn a tool name into the "read" or
"modify" this gate checks -- so a decision made here is the decision the
runtime would make, without needing a live agent run to prove it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from oc8.authz.pdp import (
    Decision,
    Effect,
    ToolPolicy,
    authorize_tool_call,
    effective_tool_policies,
    required_right,
)
from oc8.capas.discovery import find_plugin
from oc8.capas.manifest import parse_manifest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGINS_DIR = _REPO_ROOT / "capas"
_CONNECTION_KEY = "odoo"


def _real_scopes() -> dict[str, list[str]]:
    """odoo_mcp's real `[connections.scopes]`, read off disk exactly as
    `_manifest_connection` (api/v1/mcp.py) and `agent/engine.py` do -- not
    hand-rolled, so a change to tool_pack.toml's classification surfaces
    here too."""
    found = find_plugin("odoo_mcp", [str(_PLUGINS_DIR)])
    assert found is not None and found.valid, getattr(found, "error", "odoo_mcp not found")
    assert found.manifest is not None
    manifest = parse_manifest(found.manifest)
    assert manifest.tool_pack is not None
    scopes = manifest.tool_pack.connections[0].scopes
    assert isinstance(scopes, dict), "odoo_mcp is expected to use the read/modify dict form"
    return scopes


def _catalog(scopes: dict[str, list[str]]) -> list[str]:
    return [*scopes.get("read", []), *scopes.get("modify", [])]


def _policy_for_row(
    *,
    read: bool = True,
    modify: bool = True,
    approval_eur: float | None = None,
    approval_actions: list[str] | None = None,
    only: list[str] | None = None,
) -> dict[str, ToolPolicy]:
    """The frame-JSON a manual row edit actually produces, resolved through
    `effective_tool_policies` exactly like a real run's frame is -- mirrors
    `ToolPolicyWriteDTO` (api/v1/departments.py), the write-side schema this
    JSON is validated against before it ever reaches the frame."""
    frame: dict[str, Any] = {
        "tools": {
            _CONNECTION_KEY: {
                "enabled": True,
                "read": read,
                "modify": modify,
                "approval_eur": approval_eur,
                "approval_actions": approval_actions or [],
                "only": only,
            }
        }
    }
    return effective_tool_policies(frame, {})


def _decide(policies: dict[str, ToolPolicy], tool: str, scopes: dict[str, list[str]]) -> Decision:
    """The exact call shape `agent/engine.py`/`api/mcp_gateway.py` make for a
    real tool call: classify the tool via `required_right`, then gate it."""
    return authorize_tool_call(
        policies=policies,
        connection_key=_CONNECTION_KEY,
        right=required_right(tool, scopes),
        value=None,
        tool=tool,
    )


def test_manifest_classifies_every_real_tool_as_read_xor_modify() -> None:
    # The premise the disabled-checkbox UI relies on: a function is one or
    # the other, never both, never neither -- otherwise "the other checkbox
    # is just an implication, not a live control" (guardrail-function-rules.tsx)
    # would be showing a false implication.
    scopes = _real_scopes()
    read_tools, modify_tools = set(scopes["read"]), set(scopes["modify"])
    assert read_tools.isdisjoint(modify_tools)
    for tool in read_tools | modify_tools:
        assert required_right(tool, scopes) == ("read" if tool in read_tools else "modify")


def test_denying_one_function_via_only_blocks_just_that_tool() -> None:
    # Materializes what `applyMode(..., "delete_record", "deny", null)` writes
    # when a user unticks one function's checkbox: `only` becomes the whole
    # catalog minus that one name, everything else untouched.
    scopes = _real_scopes()
    only = [name for name in _catalog(scopes) if name != "delete_record"]
    policies = _policy_for_row(only=only)

    denied = _decide(policies, "delete_record", scopes)
    assert denied.effect is Effect.DENY
    assert "delete_record" in denied.reason

    still_allowed = _decide(policies, "create_record", scopes)
    assert still_allowed.effect is Effect.ALLOW
    still_readable = _decide(policies, "search_records", scopes)
    assert still_readable.effect is Effect.ALLOW


def test_approval_row_on_one_function_gates_only_that_tool() -> None:
    # Materializes `applyMode(..., "post_message", "approval", null)`: `only`
    # stays empty (nothing else restricted), `approvalActions` names the one
    # function.
    scopes = _real_scopes()
    policies = _policy_for_row(approval_actions=["post_message"])

    gated = _decide(policies, "post_message", scopes)
    assert gated.effect is Effect.REQUIRE_APPROVAL
    assert "post_message" in gated.reason

    sibling_modify_tool = _decide(policies, "create_record", scopes)
    assert sibling_modify_tool.effect is Effect.ALLOW


def test_unticking_the_base_modify_gate_blocks_every_modify_tool_but_not_reads() -> None:
    # Materializes the pinned gate row's Modify checkbox going to deny
    # (`applyGateMode(value, "modify", "deny")`): the connection-wide right
    # itself goes false, no per-function `only`/`approvalActions` involved.
    scopes = _real_scopes()
    policies = _policy_for_row(read=True, modify=False)

    for tool in scopes["modify"]:
        decision = _decide(policies, tool, scopes)
        assert decision.effect is Effect.DENY, f"{tool} should be blocked with modify off"

    for tool in scopes["read"]:
        decision = _decide(policies, tool, scopes)
        assert decision.effect is Effect.ALLOW, f"{tool} should still be readable"


def test_read_only_tool_is_denied_when_read_is_off_even_though_modify_is_on() -> None:
    # The read/modify pair is not a ladder: granting modify never implies a
    # read-classified tool becomes reachable, which is exactly why the UI
    # shows Read forced OFF (not on) for a modify-classified row and forces
    # Modify OFF for a read-classified one -- see the tooltip copy in
    # guardrail-function-rules.tsx.
    scopes = _real_scopes()
    policies = _policy_for_row(read=False, modify=True)

    decision = _decide(policies, "search_records", scopes)
    assert decision.effect is Effect.DENY
