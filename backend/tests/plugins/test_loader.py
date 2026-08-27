from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from oc8.capas import contributions, loader
from oc8.capas.discovery import MANIFEST_FILENAME, DiscoveredPlugin, find_plugin

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _clean_process_state() -> Iterator[None]:
    """The catalogue and the import cache are process-global by design (a module
    import IS process-global). Reset them around every test so one test's load
    can't satisfy another's assertion."""
    loader.reset_for_tests()
    contributions.reset_for_tests()
    yield
    loader.reset_for_tests()
    contributions.reset_for_tests()


def _discover(name: str) -> DiscoveredPlugin:
    p = find_plugin(name, [str(FIXTURES)])
    assert p is not None, f"fixture {name} not discovered"
    return p


def test_a_trusted_plugin_contributes_its_connector() -> None:
    p = _discover("demo_connector")
    assert loader.load_plugin(p) is True
    conns = contributions.connectors_for("demo_connector")
    assert "demo" in conns
    assert conns["demo"].type_id == "demo"


def test_contributions_are_keyed_by_plugin_id_not_flattened() -> None:
    """Resolution must be able to ask 'what did THIS plugin contribute' --
    a single flat global dict of usable connectors is the defect this design
    exists to prevent."""
    loader.load_plugin(_discover("demo_connector"))
    assert contributions.connectors_for("demo_connector")
    assert contributions.connectors_for("some_other_plugin") == {}


def test_an_untrusted_plugin_is_never_imported() -> None:
    """The community fixture raises AssertionError at import. Reaching the
    import at all is the failure -- trust is checked first."""
    p = _discover("community_connector")
    assert loader.load_plugin(p) is False
    assert contributions.connectors_for("community_connector") == {}
    assert loader.is_quarantined("community_connector")


def test_a_plugin_that_raises_at_import_is_quarantined_not_propagated() -> None:
    p = _discover("broken_connector")
    assert loader.load_plugin(p) is False  # no exception escapes
    assert loader.is_quarantined("broken_connector")
    assert contributions.connectors_for("broken_connector") == {}


def test_a_quarantined_plugin_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _discover("broken_connector")
    assert loader.load_plugin(p) is False

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("must not attempt the import again")

    monkeypatch.setattr(loader, "import_entry_point", _boom)
    assert loader.load_plugin(p) is False


def test_a_successful_load_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _discover("demo_connector")
    assert loader.load_plugin(p) is True

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("must not import twice")

    monkeypatch.setattr(loader, "import_entry_point", _boom)
    assert loader.load_plugin(p) is True
    assert "demo" in contributions.connectors_for("demo_connector")


def test_a_plugin_with_no_entry_points_loads_to_a_noop(tmp_path: Path) -> None:
    d = tmp_path / "dataonly"
    d.mkdir()
    (d / MANIFEST_FILENAME).write_text(
        '[plugin]\nname = "dataonly"\nversion = "1.0.0"\n'
        'type = "department_template"\ntrust = "first_party"\n'
    )
    p = find_plugin("dataonly", [str(tmp_path)])
    assert p is not None
    assert loader.load_plugin(p) is True
    assert contributions.connectors_for("dataonly") == {}
    assert not loader.is_quarantined("dataonly")


def test_a_malformed_entry_point_is_an_error_not_a_crash(tmp_path: Path) -> None:
    d = tmp_path / "badep"
    d.mkdir()
    (d / MANIFEST_FILENAME).write_text(
        '[plugin]\nname = "badep"\nversion = "1.0.0"\n'
        'type = "connector"\ntrust = "first_party"\n'
        '\n[plugin.entry_points]\nconnectors = "no_colon_here"\n'
    )
    p = find_plugin("badep", [str(tmp_path)])
    assert p is not None
    assert loader.load_plugin(p) is False
    assert loader.is_quarantined("badep")


def test_a_missing_register_function_is_an_error(tmp_path: Path) -> None:
    d = tmp_path / "noreg"
    pkg = d / "noreg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "connector.py").write_text("x = 1\n")
    (d / MANIFEST_FILENAME).write_text(
        '[plugin]\nname = "noreg"\nversion = "1.0.0"\n'
        'type = "connector"\ntrust = "first_party"\n'
        '\n[plugin.entry_points]\nconnectors = "noreg.connector:register"\n'
    )
    p = find_plugin("noreg", [str(tmp_path)])
    assert p is not None
    assert loader.load_plugin(p) is False
    assert loader.is_quarantined("noreg")


def test_an_invalid_manifest_is_never_loaded(tmp_path: Path) -> None:
    d = tmp_path / "bad"
    d.mkdir()
    (d / MANIFEST_FILENAME).write_text('[plugin]\nname = "bad"\n')  # no version
    p = find_plugin("bad", [str(tmp_path)])
    assert p is not None and p.valid is False
    assert loader.load_plugin(p) is False
