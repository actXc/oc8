from __future__ import annotations

import logging

import pytest

from oc8.capas.discovery import DiscoveredPlugin
from oc8.capas.loader import load_plugin, reset_for_tests


@pytest.fixture(autouse=True)
def _reset():
    reset_for_tests()
    yield
    reset_for_tests()


def _discovered(manifest: dict) -> DiscoveredPlugin:
    return DiscoveredPlugin(
        plugin_id=manifest["name"],
        path="/nonexistent",
        manifest=manifest,
        name=manifest["name"],
        version="1.0.0",
        type=manifest.get("type", "connector"),
        trust="first_party",
        summary="",
        valid=True,
        error=None,
    )


def test_missing_requirement_refuses_to_load_with_a_clear_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manifest = {
        "name": "x",
        "version": "1.0.0",
        "type": "connector",
        "entry_points": {},
        "requirements": ["this-package-definitely-does-not-exist-anywhere>=1.0"],
    }
    with caplog.at_level(logging.WARNING):
        result = load_plugin(_discovered(manifest))
    assert result is False
    assert any(
        # getMessage(), not .message: the latter only exists because pytest's
        # own handler happens to have formatted the record.
        "this-package-definitely-does-not-exist-anywhere" in record.getMessage()
        for record in caplog.records
    )


def test_satisfied_requirement_does_not_block_loading() -> None:
    # `packaging` is already a real dependency of this project (used by
    # oauth/tokens.py, plugins/service.py) -- genuinely importable.
    manifest = {
        "name": "y",
        "version": "1.0.0",
        "type": "connector",
        "entry_points": {},
        "requirements": ["packaging"],
    }
    result = load_plugin(_discovered(manifest))
    assert result is True


def test_no_requirements_is_unaffected() -> None:
    manifest = {
        "name": "z",
        "version": "1.0.0",
        "type": "connector",
        "entry_points": {},
    }
    result = load_plugin(_discovered(manifest))
    assert result is True
