"""Proves the three new plugins are discoverable and loadable side by side --
not just importable in isolation the way each plugin's own test file already
proves, but actually found by oc8's plugin discovery scan and resolvable
through the same runtime registry nanoclaw already goes through."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from oc8.capas import contributions, loader
from oc8.capas.discovery import discover_plugins
from oc8.capas.loader import load_plugin
from oc8.config import get_settings

# tests/plugins/cli_harness/test_registration.py -> repo root -> plugins/
PLUGINS_DIR = Path(__file__).resolve().parents[4] / "capas"

RUNTIME_PLUGIN_IDS = ("claude_code_runtime", "codex_runtime", "opencode_runtime")


@pytest.fixture(autouse=True)
def _plugins_path(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("OC8_CAPAS_PATH", str(PLUGINS_DIR))
    get_settings.cache_clear()
    loader.reset_for_tests()
    contributions.reset_for_tests()
    yield
    loader.reset_for_tests()
    contributions.reset_for_tests()
    get_settings.cache_clear()


def test_all_three_new_runtimes_are_discovered() -> None:
    discovered = {p.plugin_id for p in discover_plugins()}
    for plugin_id in RUNTIME_PLUGIN_IDS:
        assert plugin_id in discovered


def test_each_plugin_declares_the_runtime_adapter_type_and_no_checkpoints() -> None:
    by_id = {p.plugin_id: p for p in discover_plugins()}
    for name in RUNTIME_PLUGIN_IDS:
        plugin = by_id[name]
        assert plugin.manifest is not None
        assert plugin.manifest["type"] == "runtime_adapter"
        assert "checkpoints" not in plugin.manifest["capabilities"]
        assert "skills" in plugin.manifest["capabilities"]


def test_each_plugin_is_actually_loadable() -> None:
    by_id = {p.plugin_id: p for p in discover_plugins()}
    for name in RUNTIME_PLUGIN_IDS:
        assert load_plugin(by_id[name]) is True, f"{name} failed to load"
