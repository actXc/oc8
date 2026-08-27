"""Proves the plugin is discoverable and its entry point really loads and
registers the provider -- through the REAL loader, so the `plugin.toml`
entry-point string is proved too, not just a direct import of
`provider.provider`. Same pattern as opaas_ai_provider's own test."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from oc8.capas import contributions, loader
from oc8.capas.discovery import find_plugin
from oc8.config import get_settings

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = PLUGIN_ROOT.parent


def _evict() -> None:
    """Drop every cached `provider`/`provider.*` module from sys.modules --
    `provider` is the folder convention for a `model_adapter` plugin, so it is
    a name any future model adapter also ships, and sys.modules is keyed by
    NAME, not by path."""
    for _stale in [n for n in sys.modules if n == "provider" or n.startswith("provider.")]:
        del sys.modules[_stale]


@pytest.fixture(autouse=True)
def _plugin_path(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("OC8_CAPAS_PATH", str(PLUGINS_ROOT))
    get_settings.cache_clear()
    loader.reset_for_tests()
    contributions.reset_for_tests()
    _evict()
    sys.path.insert(0, str(PLUGIN_ROOT))
    yield
    sys.path.remove(str(PLUGIN_ROOT))
    _evict()
    loader.reset_for_tests()
    contributions.reset_for_tests()
    get_settings.cache_clear()


def test_the_manifest_entry_point_really_loads_and_registers_the_provider() -> None:
    found = find_plugin("openrouter_provider")
    assert found is not None, "openrouter_provider was not discovered at all"
    assert found.valid, found.error
    assert found.manifest is not None
    assert found.manifest["entry_points"]["providers"] == "provider.provider:register"

    assert loader.load_plugin(found) is True
    assert "openrouter" in contributions.providers_for("openrouter_provider")


def test_the_credential_type_is_discovered() -> None:
    """The BYOK key a tenant enters for this provider goes through the unified
    credentials framework (a `Credential` of THIS plugin's own
    `openrouter_api_key` type, credential_types/openrouter_api_key.toml) --
    `resolve_model_key` (oc8.modelrouter.keys) already resolves any
    `{canonical}_api_key` type generically by name, so the one thing this
    plugin still has to supply for that resolution to ever find a row is the
    type declaration itself."""
    found = find_plugin("openrouter_provider")
    assert found is not None
    assert found.valid, found.error
    assert found.manifest is not None
    types = found.manifest["credential_types"]
    assert [t["name"] for t in types] == ["openrouter_api_key"]
    fields = {f["key"]: f for f in types[0]["fields"]}
    assert fields["api_key"]["kind"] == "password"
    assert "base_url" not in fields
