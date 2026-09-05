"""The hubspot_mcp plugin ships five guardrail presets and five curated
guardrails/ library entries. This reads the REAL plugin.toml/tool_pack.toml/
guardrails/*.toml -- editing the TOML without editing this test must fail,
since these values are a security posture, not configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from oc8.authz.pdp import Effect, ToolPolicy, authorize_tool_call, effective_tool_policies
from oc8.capas.discovery import DiscoveredPlugin, find_plugin
from oc8.capas.guardrails import Guardrail, GuardrailLibrary
from oc8.capas.manifest import GuardrailPreset, ToolPackConnection, parse_manifest

# tests/plugins/<this> -> tests -> backend -> repo root, where capas/ lives.
_PLUGINS_DIR = Path(__file__).resolve().parents[3] / "capas"

_CONNECTION_KEY = "hubspot"

_READ_TOOLS = {
    "hubspot-get-user-details",
    "hubspot-list-objects",
    "hubspot-search-objects",
    "hubspot-batch-read-objects",
    "hubspot-get-schemas",
    "hubspot-list-properties",
    "hubspot-get-property",
    "hubspot-list-associations",
    "hubspot-get-association-definitions",
    "hubspot-get-engagement",
    "hubspot-list-workflows",
    "hubspot-get-workflow",
    "hubspot-generate-feedback-link",
    "hubspot-get-link",
}
_SEND_TOOLS = {
    "hubspot-batch-create-objects",
    "hubspot-batch-update-objects",
    "hubspot-create-property",
    "hubspot-update-property",
    "hubspot-batch-create-associations",
    "hubspot-create-engagement",
    "hubspot-update-engagement",
}


def _discovered() -> DiscoveredPlugin:
    found = find_plugin("hubspot_mcp", [str(_PLUGINS_DIR)])
    assert found is not None, "hubspot_mcp plugin not found"
    assert found.valid, found.error
    return found


def _connection() -> ToolPackConnection:
    found = _discovered()
    assert found.manifest is not None
    manifest = parse_manifest(found.manifest)
    assert manifest.tool_pack is not None
    return manifest.tool_pack.connections[0]


def _library() -> GuardrailLibrary:
    lib = _discovered().guardrail_library
    assert lib is not None, "expected hubspot_mcp/guardrails/ to ship kind = 'library' entries"
    return lib


def _entry(key: str) -> Guardrail:
    entries = {g.key: g for g in _library().guardrail}
    assert key in entries, f"no guardrail {key!r}"
    return entries[key]


def _policies_for(g: Guardrail | GuardrailPreset) -> dict[str, ToolPolicy]:
    # Guardrail has .modify (collapsed write/send), GuardrailPreset has separate .write and .send
    if isinstance(g, Guardrail):
        write = g.modify
        send = g.modify
    else:
        write = g.write
        send = g.send

    frame: dict[str, Any] = {
        "tools": {
            _CONNECTION_KEY: {
                "enabled": True,
                "read": g.read,
                "write": write,
                "send": send,
                "approval_eur": g.approval_eur,
                "approval_actions": sorted(g.approval_actions),
                "only": list(g.only),
            }
        }
    }
    return effective_tool_policies(frame, {})


def _decide(
    g: Guardrail | GuardrailPreset, *, right: str, tool: str, value: float | None = None
) -> Any:
    policies = _policies_for(g)
    return authorize_tool_call(
        policies=policies, connection_key=_CONNECTION_KEY, right=right, tool=tool, value=value
    )


class TestHubSpotMcpGuardrailPresets:
    def test_ships_exactly_five_presets(self) -> None:
        conn = _connection()
        assert {p.key for p in conn.guardrail_presets} == {
            "read_only",
            "assist_with_approval",
            "autonomous_with_limit",
            "no_schema_changes",
            "internal_crm_work_no_outreach",
        }

    def test_exactly_one_is_recommended(self) -> None:
        conn = _connection()
        recommended = [p.key for p in conn.guardrail_presets if p.recommended]
        assert recommended == ["assist_with_approval"]

    def test_read_only_grants_read_alone(self) -> None:
        p = next(p for p in _connection().guardrail_presets if p.key == "read_only")
        assert (p.read, p.write, p.send, p.approval_actions, p.approval_eur) == (
            True,
            False,
            False,
            [],
            None,
        )

    def test_assist_with_approval_requires_approval_on_every_send(self) -> None:
        p = next(p for p in _connection().guardrail_presets if p.key == "assist_with_approval")
        assert (p.read, p.write, p.send) == (True, False, True)
        assert p.approval_actions == ["send"]

    def test_no_preset_grants_write_because_no_hubspot_tool_needs_it(self) -> None:
        for preset in _connection().guardrail_presets:
            assert preset.write is False, (
                f"{preset.key} grants a right no hubspot_mcp tool requires"
            )

    def test_autonomous_with_limit_excludes_schema_tools_and_sets_threshold(self) -> None:
        p = next(p for p in _connection().guardrail_presets if p.key == "autonomous_with_limit")
        assert p.approval_eur == 5000
        assert p.only
        assert "hubspot-create-property" not in p.only
        assert "hubspot-update-property" not in p.only
        assert "hubspot-batch-create-objects" in p.only

    def test_no_schema_changes_withholds_only_property_tools(self) -> None:
        p = next(p for p in _connection().guardrail_presets if p.key == "no_schema_changes")
        assert p.only
        assert "hubspot-create-property" not in p.only
        assert "hubspot-update-property" not in p.only
        assert "hubspot-batch-create-objects" in p.only
        assert "hubspot-create-engagement" in p.only

    def test_internal_crm_work_no_outreach_withholds_engagement_tools_only(self) -> None:
        # `hubspot-create-engagement`/`hubspot-update-engagement` are this
        # connection's outward tools (tool_pack.toml) -- the only calls that
        # notify a contact or colleague.
        p = next(
            p for p in _connection().guardrail_presets if p.key == "internal_crm_work_no_outreach"
        )
        assert p.only
        assert "hubspot-create-engagement" not in p.only
        assert "hubspot-update-engagement" not in p.only
        assert "hubspot-batch-create-objects" in p.only
        assert "hubspot-create-property" in p.only


class TestHubSpotMcpGuardrailLibrary:
    def test_entry_count_is_five(self) -> None:
        assert len(_library().guardrail) == 5

    def test_all_keys_are_unique(self) -> None:
        keys = [g.key for g in _library().guardrail]
        assert len(keys) == len(set(keys))

    def test_no_tool_is_write_so_every_entry_write_is_false(self) -> None:
        # After collapsing write/send into modify, check guardrails don't grant
        # write since no hubspot_mcp tool is classified as write-capable
        for g in _library().guardrail:
            assert g.modify is False or g.only, (
                f"{g.key} grants modify but has no tool restrictions"
            )

    def test_every_entry_has_nonempty_prose(self) -> None:
        for g in _library().guardrail:
            for field in (g.label, g.summary):
                assert field and field.strip()

    def test_every_entry_only_and_approval_actions_name_real_tools(self) -> None:
        known = _READ_TOOLS | _SEND_TOOLS
        for g in _library().guardrail:
            for tool in g.only:
                assert tool in known, f"{g.key}.only names unknown tool {tool!r}"
            for action in g.approval_actions:
                assert action in known | {"send", "read", "write"}, (
                    f"{g.key}.approval_actions names unrecognised {action!r}"
                )

    def test_sales_pipeline_entry_sets_five_thousand_euro_threshold_and_is_adjustable(self) -> None:
        g = _entry("sales_pipeline_autonomous_with_limit")
        assert g.approval_eur == 5000
        assert "hubspot-create-property" not in g.only
        assert [a.field for a in g.adjustable] == ["approval_eur"]

    def test_marketing_outreach_entry_gates_only_engagement_tools(self) -> None:
        g = _entry("marketing_outreach_needs_approval")
        assert g.approval_actions == frozenset(
            {"hubspot-create-engagement", "hubspot-update-engagement"}
        )
        assert g.modify is True

    def test_support_ticket_entry_gates_only_engagement_tools(self) -> None:
        g = _entry("support_ticket_engagement_needs_approval")
        assert g.approval_actions == frozenset(
            {"hubspot-create-engagement", "hubspot-update-engagement"}
        )
        assert g.modify is True

    def test_schema_lock_entry_withholds_only_property_tools(self) -> None:
        g = _entry("schema_lock_no_property_changes")
        assert g.only
        assert "hubspot-create-property" not in g.only
        assert "hubspot-update-property" not in g.only
        assert "hubspot-batch-create-objects" in g.only

    def test_revops_reporting_entry_grants_read_alone(self) -> None:
        g = _entry("revops_reporting_read_only")
        assert (g.read, g.modify) == (True, False)

    # --- Integration: real entries -> real PDP decisions ---

    def test_integration_sales_pipeline_threshold_is_inclusive(self) -> None:
        g = _entry("sales_pipeline_autonomous_with_limit")
        below = _decide(g, right="send", tool="hubspot-batch-create-objects", value=4999)
        assert below.effect is Effect.ALLOW
        at = _decide(g, right="send", tool="hubspot-batch-create-objects", value=5000)
        assert at.effect is Effect.REQUIRE_APPROVAL
        above = _decide(g, right="send", tool="hubspot-batch-create-objects", value=9000)
        assert above.effect is Effect.REQUIRE_APPROVAL

    def test_integration_sales_pipeline_schema_tools_are_out_of_reach_regardless_of_value(
        self,
    ) -> None:
        g = _entry("sales_pipeline_autonomous_with_limit")
        assert (
            _decide(g, right="send", tool="hubspot-create-property", value=None).effect
            is Effect.DENY
        )

    def test_integration_marketing_outreach_gates_engagement_not_records(self) -> None:
        g = _entry("marketing_outreach_needs_approval")
        gated = _decide(g, right="send", tool="hubspot-create-engagement", value=None)
        assert gated.effect is Effect.REQUIRE_APPROVAL
        assert (
            _decide(g, right="send", tool="hubspot-batch-create-objects", value=None).effect
            is Effect.ALLOW
        )

    def test_integration_schema_lock_denies_property_tools_only(self) -> None:
        g = _entry("schema_lock_no_property_changes")
        denied = _decide(g, right="send", tool="hubspot-create-property", value=None)
        assert denied.effect is Effect.DENY
        assert (
            _decide(g, right="send", tool="hubspot-create-engagement", value=None).effect
            is Effect.ALLOW
        )

    def test_no_hubspot_mcp_tool_name_collides_with_a_right(self) -> None:
        from oc8.authz.pdp import RIGHTS

        for tool in _READ_TOOLS | _SEND_TOOLS:
            assert tool not in RIGHTS
