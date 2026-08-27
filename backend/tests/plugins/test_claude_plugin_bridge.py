from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from oc8.capas.claude_adapter import adapt_claude_plugin, is_claude_plugin, merge_claude_and_oc8
from oc8.capas.claude_hooks import (
    dispatch_claude_event,
    register_claude_hooks_for_capa,
    reset_claude_hook_registries_for_tests,
)
from oc8.capas.claude_hooks.executors import ClaudeHookExecutors, matcher_matches
from oc8.capas.discovery import discover_plugins, invalidate_discovery_cache
from oc8.capas.manifest import ClaudeHooksSpec, parse_manifest


FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    invalidate_discovery_cache()
    reset_claude_hook_registries_for_tests()
    yield
    invalidate_discovery_cache()
    reset_claude_hook_registries_for_tests()


def test_is_claude_plugin_detects_layout() -> None:
    root = FIXTURES / "claude_only_plugin"
    assert is_claude_plugin(root) is True
    assert is_claude_plugin(FIXTURES / "demo_hook") is False


def test_adapt_claude_only_plugin() -> None:
    root = FIXTURES / "claude_only_plugin"
    table, warnings = adapt_claude_plugin(root, plugin_id="claude_only_plugin")
    assert table["name"] == "claude_only_plugin"
    assert table["source_format"] == "claude"
    assert table["trust"] == "community"
    assert table["type"] == "agent_template"
    assert table["agent_template"]["name"] == "Riley"
    assert table["tool_pack"]["connections"][0]["key"] == "demo_server"
    assert table["claude_hooks"]["events"]["PreToolUse"]
    assert "hooks:claude:command" in table["permissions"]
    assert warnings == []


def test_discover_claude_only_without_plugin_toml(tmp_path: Path) -> None:
    shutil.copytree(FIXTURES / "claude_only_plugin", tmp_path / "claude_only_plugin")
    found = discover_plugins([str(tmp_path)])
    assert len(found) == 1
    p = found[0]
    assert p.valid is True
    assert p.plugin_id == "claude_only_plugin"
    assert p.manifest is not None
    assert p.manifest["source_format"] == "claude"
    assert p.type == "agent_template"
    assert any(s["name"] == "Code Review" for s in p.manifest["skill_pack"]["skills"])


def test_discover_hybrid_plugin(tmp_path: Path) -> None:
    shutil.copytree(FIXTURES / "claude_hybrid_plugin", tmp_path / "claude_hybrid_plugin")
    found = discover_plugins([str(tmp_path)])
    assert len(found) == 1
    mf = found[0].manifest
    assert mf is not None
    assert mf["source_format"] == "hybrid"
    assert mf["trust"] == "first_party"
    assert mf["agent_template"]["mission"].startswith("Oc8 overlay")
    assert any(s["name"] == "Extra Skill" for s in mf["skill_pack"]["skills"])


def test_merge_skill_collision_raises() -> None:
    claude = {"skill_pack": {"skills": [{"name": "Same", "instruction": "a"}]}}
    oc8 = {"skill_pack": {"skills": [{"name": "Same", "instruction": "b"}]}}
    with pytest.raises(Exception, match="duplicate skill"):
        merge_claude_and_oc8(claude, oc8)


def test_matcher_matches_tool_names() -> None:
    assert matcher_matches("Write|Edit", "Write") is True
    assert matcher_matches("Write|Edit", "Read") is False
    assert matcher_matches("", "anything") is True


@pytest.mark.asyncio
async def test_pre_tool_use_command_hook_blocks() -> None:
    tenant_id = uuid.uuid4()
    root = FIXTURES / "claude_only_plugin"
    table, _ = adapt_claude_plugin(root, plugin_id="claude_only_plugin")
    register_claude_hooks_for_capa(
        tenant_id,
        capa_id="capa-1",
        plugin_name="claude_only_plugin",
        capa_path=str(root),
        trust_level="community",
        granted_permissions=["hooks:claude:command"],
        claude_hooks=table["claude_hooks"],
    )
    result = await dispatch_claude_event(
        tenant_id,
        "PreToolUse",
        {"tool_name": "dangerous_tool", "tool_input": {}},
        tool_name="dangerous_tool",
    )
    assert result.blocked is True
    assert "blocked" in result.reason.lower()


def test_claude_hooks_spec_in_manifest() -> None:
    spec = ClaudeHooksSpec(events={"Stop": []})
    mf = parse_manifest({"name": "x", "version": "1", "claude_hooks": spec.model_dump()})
    assert mf.claude_hooks is not None
    assert mf.source_format == "oc8"


@pytest.mark.asyncio
async def test_command_hook_requires_permission() -> None:
    executors = ClaudeHookExecutors(
        capa_path="/tmp",
        granted_permissions=[],
        trust_level="community",
    )
    from oc8.capas.manifest import ClaudeHookAction

    result = await executors.run(
        ClaudeHookAction(type="command", command="echo hi"),
        {"tool_name": "t"},
    )
    assert result.blocked is False
