"""The github_mcp plugin ships six guardrail presets and four curated
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

_CONNECTION_KEY = "github"

_READ_TOOLS = {
    "get_me",
    "get_team_members",
    "get_teams",
    "get_label",
    "issue_read",
    "list_issue_fields",
    "list_issue_types",
    "list_issues",
    "search_issues",
    "list_pull_requests",
    "pull_request_read",
    "search_pull_requests",
    "get_commit",
    "get_file_contents",
    "get_latest_release",
    "get_release_by_tag",
    "get_tag",
    "list_branches",
    "list_commits",
    "list_releases",
    "list_repository_collaborators",
    "list_tags",
    "search_code",
    "search_commits",
    "search_repositories",
    "search_users",
}
_SEND_TOOLS = {
    "add_issue_comment",
    "issue_write",
    "sub_issue_write",
    "add_comment_to_pending_review",
    "add_reply_to_pull_request_comment",
    "create_pull_request",
    "merge_pull_request",
    "pull_request_review_write",
    "update_pull_request",
    "update_pull_request_branch",
    "create_branch",
    "create_or_update_file",
    "create_repository",
    "delete_file",
    "fork_repository",
    "push_files",
}


def _discovered() -> DiscoveredPlugin:
    found = find_plugin("github_mcp", [str(_PLUGINS_DIR)])
    assert found is not None, "github_mcp plugin not found"
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
    assert lib is not None, "expected github_mcp/guardrails/ to ship kind = 'library' entries"
    return lib


def _entry(key: str) -> Guardrail:
    entries = {g.key: g for g in _library().guardrail}
    assert key in entries, f"no guardrail {key!r}"
    return entries[key]


def _policies_for(g: Guardrail | GuardrailPreset) -> dict[str, ToolPolicy]:
    frame: dict[str, Any] = {
        "tools": {
            _CONNECTION_KEY: {
                "enabled": True,
                "read": g.read,
                "write": g.write,
                "send": g.send,
                "approval_eur": g.approval_eur,
                "approval_actions": sorted(g.approval_actions),
                "only": list(g.only),
            }
        }
    }
    return effective_tool_policies(frame, {})


class TestGitHubMcpGuardrailPresets:
    def test_ships_exactly_six_presets(self) -> None:
        conn = _connection()
        assert {p.key for p in conn.guardrail_presets} == {
            "read_only",
            "assist_with_approval",
            "no_deletions",
            "no_merge",
            "docs_only",
            "autonomous_dev_work",
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

    def test_no_preset_grants_write_because_no_github_tool_needs_it(self) -> None:
        # tool_pack.toml's `scopes` classify every tool as `read` or `send`;
        # not one is `write` -- granting it would advertise a capability no
        # tool confers.
        for preset in _connection().guardrail_presets:
            assert preset.write is False, f"{preset.key} grants a right no github_mcp tool requires"

    def test_no_deletions_withholds_delete_file_but_not_comments(self) -> None:
        p = next(p for p in _connection().guardrail_presets if p.key == "no_deletions")
        assert p.only, "an empty allowlist would put delete_file back within reach"
        assert "delete_file" not in p.only
        assert "add_issue_comment" in p.only

    def test_no_merge_withholds_merge_and_delete_but_not_pr_creation(self) -> None:
        p = next(p for p in _connection().guardrail_presets if p.key == "no_merge")
        assert p.only
        assert "merge_pull_request" not in p.only
        assert "delete_file" not in p.only
        assert "create_pull_request" in p.only

    def test_docs_only_withholds_code_and_pr_tools(self) -> None:
        p = next(p for p in _connection().guardrail_presets if p.key == "docs_only")
        assert p.only
        assert "create_pull_request" not in p.only
        assert "create_or_update_file" not in p.only
        assert "push_files" not in p.only
        assert "add_issue_comment" in p.only

    def test_autonomous_dev_work_grants_full_send_without_approval(self) -> None:
        p = next(p for p in _connection().guardrail_presets if p.key == "autonomous_dev_work")
        assert (p.read, p.write, p.send, p.approval_actions, p.only) == (
            True,
            False,
            True,
            [],
            [],
        )


class TestGitHubMcpGuardrailLibrary:
    def test_entry_count_is_four(self) -> None:
        assert len(_library().guardrail) == 4

    def test_all_keys_are_unique(self) -> None:
        keys = [g.key for g in _library().guardrail]
        assert len(keys) == len(set(keys))

    def test_no_tool_is_write_so_every_entry_write_is_false(self) -> None:
        for g in _library().guardrail:
            assert g.write is False

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

    def test_issue_triage_entry_withholds_all_code_and_pr_tools(self) -> None:
        g = _entry("issue_triage_no_pr")
        assert set(g.only) == {"add_issue_comment", "issue_write", "sub_issue_write"}
        assert g.send is True
        assert not g.approval_actions

    def test_dev_assistant_entry_gates_only_merge_pull_request(self) -> None:
        g = _entry("dev_assistant_merge_needs_approval")
        assert g.approval_actions == frozenset({"merge_pull_request"})
        assert g.send is True
        assert not g.only

    def test_repo_admin_entry_gates_only_repo_creation_and_forking(self) -> None:
        g = _entry("repo_admin_needs_approval")
        assert g.approval_actions == frozenset({"create_repository", "fork_repository"})
        assert g.send is True
        assert not g.only

    def test_cross_never_deletes_withholds_only_delete_file(self) -> None:
        g = _entry("cross_never_deletes")
        assert g.only
        assert "delete_file" not in g.only
        assert "add_issue_comment" in g.only

    # --- Integration: real entries -> real PDP decisions ---

    def test_integration_dev_assistant_entry_gates_merge_not_other_writes(self) -> None:
        key = "dev_assistant_merge_needs_approval"
        policies = _policies_for(_entry(key))
        gated = authorize_tool_call(
            policies=policies,
            connection_key=_CONNECTION_KEY,
            right="send",
            tool="merge_pull_request",
            value=None,
        )
        assert gated.effect is Effect.REQUIRE_APPROVAL
        assert (
            authorize_tool_call(
                policies=policies,
                connection_key=_CONNECTION_KEY,
                right="send",
                tool="create_pull_request",
                value=None,
            ).effect
            is Effect.ALLOW
        )
        assert (
            authorize_tool_call(
                policies=policies,
                connection_key=_CONNECTION_KEY,
                right="send",
                tool="add_issue_comment",
                value=None,
            ).effect
            is Effect.ALLOW
        )

    def test_integration_repo_admin_entry_gates_creation_not_everyday_work(self) -> None:
        key = "repo_admin_needs_approval"
        policies = _policies_for(_entry(key))
        gated = authorize_tool_call(
            policies=policies,
            connection_key=_CONNECTION_KEY,
            right="send",
            tool="create_repository",
            value=None,
        )
        assert gated.effect is Effect.REQUIRE_APPROVAL
        assert (
            authorize_tool_call(
                policies=policies,
                connection_key=_CONNECTION_KEY,
                right="send",
                tool="fork_repository",
                value=None,
            ).effect
            is Effect.REQUIRE_APPROVAL
        )
        assert (
            authorize_tool_call(
                policies=policies,
                connection_key=_CONNECTION_KEY,
                right="send",
                tool="create_pull_request",
                value=None,
            ).effect
            is Effect.ALLOW
        )

    def test_integration_cross_never_deletes_denies_delete_file_only(self) -> None:
        key = "cross_never_deletes"
        policies = _policies_for(_entry(key))
        denied = authorize_tool_call(
            policies=policies,
            connection_key=_CONNECTION_KEY,
            right="send",
            tool="delete_file",
            value=None,
        )
        assert denied.effect is Effect.DENY
        assert (
            authorize_tool_call(
                policies=policies,
                connection_key=_CONNECTION_KEY,
                right="send",
                tool="add_issue_comment",
                value=None,
            ).effect
            is Effect.ALLOW
        )

    def test_no_github_mcp_tool_name_collides_with_a_right(self) -> None:
        from oc8.authz.pdp import RIGHTS

        for tool in _READ_TOOLS | _SEND_TOOLS:
            assert tool not in RIGHTS
