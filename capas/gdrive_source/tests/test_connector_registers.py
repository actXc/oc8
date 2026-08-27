"""Proves the retrofitted `connector/` package is importable at its new path
and still registers -- and, equally, that a plugin-local tests/ folder is
collected, type-checked and lintable outside backend/tests/ (the mechanism
Tasks 12-13 rely on for the two big suites)."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from oc8.capas.contributions import PluginContributions

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _evict() -> None:
    """Drop any cached `connector`/`connector.*` module from sys.modules."""
    for _stale in [n for n in sys.modules if n == "connector" or n.startswith("connector.")]:
        del sys.modules[_stale]


@pytest.fixture(autouse=True)
def _plugin_path() -> Iterator[None]:
    """`connector` is a generic name every connector plugin reuses -- evict any
    sibling plugin's cached copy first. Function-scoped and autouse, per Task
    7's convention: eviction has to happen around the import that actually
    resolves, and in this repo most plugin-test imports resolve inside a test
    body, at execution time. Read Task 7's report before changing this.

    Eviction is SYMMETRIC -- setup AND teardown, deliberately, not setup-only.
    `loader.import_entry_point` (Task 6's collision fix) only evicts modules
    IT ITSELF introduced during its own import call; if `connector` is already
    cached from a prior test's teardown, `importlib.import_module` returns the
    cached module, the loader's `introduced` set is empty, and it silently
    hands back the WRONG plugin's `register`. Reproduced live during review:
    without a teardown evict, a second `connector/`-shipping plugin's loader
    call resolved to *this* plugin's code. Leave no generic name cached behind
    for the loader to reuse."""
    sys.path.insert(0, str(PLUGIN_ROOT))
    _evict()
    yield
    sys.path.remove(str(PLUGIN_ROOT))
    _evict()


def test_register_contributes_the_drive_connector() -> None:
    # Function-local, INSIDE the fixture's window -- the fixture removes
    # PLUGIN_ROOT from sys.path again on teardown, so a module-top import here
    # would resolve before the fixture ever ran.
    from connector.connector import register

    contrib = PluginContributions(plugin_id="gdrive_source")
    register(contrib)
    assert "gdrive" in contrib.connectors
