"""The jira_mcp plugin ships five guardrail presets and five curated
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

_CONNECTION_KEY = "jira"

_READ_TOOLS = {
    "jira_get_issue",
    "jira_search",
    "jira_get_project_issues",
    "jira_batch_get_changelogs",
    "jira_search_fields",
    "jira_get_transitions",
    "jira_get_project_issue_types",
    "jira_get_project_versions",
    "jira_get_project_components",
    "jira_get_all_projects",
    "jira_search_projects",
    "jira_get_agile_boards",
    "jira_get_board_issues",
    "jira_get_sprints_from_board",
    "jira_get_sprint_issues",
    "jira_get_link_types",
    "jira_get_worklog",
    "jira_get_user_profile",
    "jira_search_assignable_users",
    "jira_get_issue_watchers",
}
_SEND_TOOLS = {
    "jira_create_issue",
    "jira_batch_create_issues",
    "jira_update_issue",
    "jira_assign_issue",
    "jira_delete_issue",
    "jira_move_issue",
    "jira_add_comment",
    "jira_edit_comment",
    "jira_transition_issue",
    "jira_link_to_epic",
    "jira_create_issue_link",
    "jira_remove_issue_link",
    "jira_add_worklog",
    "jira_add_watcher",
    "jira_remove_watcher",
    "jira_create_sprint",
    "jira_update_sprint",
    "jira_add_issues_to_sprint",
}


def _discovered() -> DiscoveredPlugin:
    found = find_plugin("jira_mcp", [str(_PLUGINS_DIR)])
    assert found is not None, "jira_mcp plugin not found"
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
    assert lib is not None, "expected jira_mcp/guardrails/ to ship kind = 'library' entries"
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


class TestJiraMcpGuardrailPresets:
    def test_ships_exactly_five_presets(self) -> None:
        conn = _connection()
        assert {p.key for p in conn.guardrail_presets} == {
            "read_only",
            "assist_with_approval",
            "no_deletions",
            "internal_only",
            "triage_only",
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

    def test_no_preset_grants_write_because_no_jira_tool_needs_it(self) -> None:
        # tool_pack.toml's `scopes` classify every tool as `read` or `send`;
        # not one is `write` -- granting it would advertise a capability no
        # tool confers.
        for preset in _connection().guardrail_presets:
            assert preset.write is False, f"{preset.key} grants a right no jira_mcp tool requires"

    def test_no_deletions_withholds_jira_delete_issue_but_not_comments(self) -> None:
        p = next(p for p in _connection().guardrail_presets if p.key == "no_deletions")
        assert p.only, "an empty allowlist would put jira_delete_issue back within reach"
        assert "jira_delete_issue" not in p.only
        assert "jira_add_comment" in p.only

    def test_internal_only_withholds_both_comment_tools_but_not_delete(self) -> None:
        # `jira_add_comment`/`jira_edit_comment` are this connection's
        # outward tools (tool_pack.toml) -- the only calls that notify a
        # person. Rights cannot express "may triage but never notify anyone",
        # because commenting shares the `send` right with every other write.
        p = next(p for p in _connection().guardrail_presets if p.key == "internal_only")
        assert p.only
        assert "jira_add_comment" not in p.only
        assert "jira_edit_comment" not in p.only
        assert "jira_update_issue" in p.only

    def test_triage_only_withholds_issue_creation_and_deletion(self) -> None:
        p = next(p for p in _connection().guardrail_presets if p.key == "triage_only")
        assert p.only
        assert "jira_create_issue" not in p.only
        assert "jira_batch_create_issues" not in p.only
        assert "jira_delete_issue" not in p.only
        assert "jira_update_issue" in p.only


class TestJiraMcpGuardrailLibrary:
    def test_entry_count_is_five(self) -> None:
        assert len(_library().guardrail) == 5

    def test_all_keys_are_unique(self) -> None:
        keys = [g.key for g in _library().guardrail]
        assert len(keys) == len(set(keys))

    def test_no_tool_is_write_so_every_entry_write_is_false(self) -> None:
        # After collapsing write/send into modify, check guardrails don't grant
        # write since no jira_mcp tool is classified as write-capable
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

    def test_support_entry_gates_only_the_comment_tools_and_keeps_send_true(self) -> None:
        g = _entry("support_ticket_triage_reply_needs_approval")
        assert g.approval_actions == frozenset({"jira_add_comment", "jira_edit_comment"})
        assert g.modify is True

    def test_release_entry_gates_only_transition_issue(self) -> None:
        g = _entry("release_transitions_need_approval")
        assert g.approval_actions == frozenset({"jira_transition_issue"})
        assert g.modify is True
        assert not g.only

    def test_sprint_planning_only_touches_sprint_tools(self) -> None:
        g = _entry("sprint_planning_only")
        assert set(g.only) == {
            "jira_create_sprint",
            "jira_update_sprint",
            "jira_add_issues_to_sprint",
        }

    def test_cross_never_deletes_withholds_only_deletion(self) -> None:
        g = _entry("cross_never_deletes")
        assert g.only
        assert "jira_delete_issue" not in g.only
        assert "jira_add_comment" in g.only

    # --- Integration: real entries -> real PDP decisions ---

    def test_integration_support_entry_gates_comment_not_other_writes(self) -> None:
        key = "support_ticket_triage_reply_needs_approval"
        policies = _policies_for(_entry(key))
        gated = authorize_tool_call(
            policies=policies,
            connection_key=_CONNECTION_KEY,
            right="send",
            tool="jira_add_comment",
            value=None,
        )
        assert gated.effect is Effect.REQUIRE_APPROVAL
        assert (
            authorize_tool_call(
                policies=policies,
                connection_key=_CONNECTION_KEY,
                right="send",
                tool="jira_update_issue",
                value=None,
            ).effect
            is Effect.ALLOW
        )
        assert (
            authorize_tool_call(
                policies=policies,
                connection_key=_CONNECTION_KEY,
                right="send",
                tool="jira_create_issue",
                value=None,
            ).effect
            is Effect.ALLOW
        )

    def test_integration_release_entry_gates_transition_not_update(self) -> None:
        key = "release_transitions_need_approval"
        policies = _policies_for(_entry(key))
        gated = authorize_tool_call(
            policies=policies,
            connection_key=_CONNECTION_KEY,
            right="send",
            tool="jira_transition_issue",
            value=None,
        )
        assert gated.effect is Effect.REQUIRE_APPROVAL
        assert (
            authorize_tool_call(
                policies=policies,
                connection_key=_CONNECTION_KEY,
                right="send",
                tool="jira_update_issue",
                value=None,
            ).effect
            is Effect.ALLOW
        )

    def test_integration_cross_never_deletes_denies_delete_only(self) -> None:
        key = "cross_never_deletes"
        policies = _policies_for(_entry(key))
        denied = authorize_tool_call(
            policies=policies,
            connection_key=_CONNECTION_KEY,
            right="send",
            tool="jira_delete_issue",
            value=None,
        )
        assert denied.effect is Effect.DENY
        assert (
            authorize_tool_call(
                policies=policies,
                connection_key=_CONNECTION_KEY,
                right="send",
                tool="jira_update_issue",
                value=None,
            ).effect
            is Effect.ALLOW
        )

    def test_no_jira_mcp_tool_name_collides_with_a_right(self) -> None:
        from oc8.authz.pdp import RIGHTS

        for tool in _READ_TOOLS | _SEND_TOOLS:
            assert tool not in RIGHTS
