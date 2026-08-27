"""The plugin must be discoverable, loadable, and HONEST about capabilities.

Declaring `checkpoints` would let a supervised agent be assigned to a runtime
that cannot resume from a checkpoint -- the negotiation is fail-closed precisely
so that claim has to be true.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from oc8.capas import contributions, loader
from oc8.capas.discovery import find_plugin
from oc8.config import get_settings
from oc8.runtime.registry import (
    RUNTIME_CAPABILITY_CHECKPOINTS,
    RUNTIME_CAPABILITY_SKILLS,
    check_runtime_capabilities,
    is_runtime_executable,
)

# tests/plugins/nanoclaw/test_registry.py -> repo root -> plugins/
PLUGINS_DIR = Path(__file__).resolve().parents[4] / "capas"


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


def test_the_plugin_is_discoverable_and_is_a_runtime_adapter() -> None:
    discovered = find_plugin("nanoclaw_runtime")

    assert discovered is not None
    assert discovered.manifest is not None
    assert discovered.manifest["type"] == "runtime_adapter"


def test_the_runtime_has_an_implementation() -> None:
    assert is_runtime_executable("nanoclaw_runtime") is True


def test_it_claims_skills_but_not_checkpoints() -> None:
    discovered = find_plugin("nanoclaw_runtime")
    assert discovered is not None
    assert discovered.manifest is not None
    caps = discovered.manifest["capabilities"]

    assert RUNTIME_CAPABILITY_SKILLS in caps
    assert RUNTIME_CAPABILITY_CHECKPOINTS not in caps
    assert (
        check_runtime_capabilities(
            has_supervision=True, has_enabled_skills=False, runtime_capabilities=caps
        )
        != []
    ), "a supervised agent must still be refused"
    assert (
        check_runtime_capabilities(
            has_supervision=False, has_enabled_skills=True, runtime_capabilities=caps
        )
        == []
    ), "an agent with skills is fine — we serve them through CLAUDE.md"
