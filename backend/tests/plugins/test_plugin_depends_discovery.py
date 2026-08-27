# backend/tests/plugins/test_plugin_depends_discovery.py
from __future__ import annotations

from pathlib import Path

from oc8.capas.discovery import discover_plugins


def _plugin(folder: Path, name: str, depends: list[str]) -> None:
    plugin_dir = folder / name
    plugin_dir.mkdir()
    depends_toml = repr(depends).replace("'", '"')
    (plugin_dir / "plugin.toml").write_text(
        f"""
[plugin]
name = "{name}"
version = "1.0.0"
type = "connector"
trust = "first_party"
summary = "x"
plugin_depends = {depends_toml}
""",
        encoding="utf-8",
    )


def test_no_cycle_is_fine(tmp_path: Path) -> None:
    _plugin(tmp_path, "a", ["b"])
    _plugin(tmp_path, "b", [])
    plugins = discover_plugins([str(tmp_path)])
    assert all(p.valid for p in plugins), [p.error for p in plugins if not p.valid]


def test_a_two_plugin_cycle_invalidates_both(tmp_path: Path) -> None:
    _plugin(tmp_path, "a", ["b"])
    _plugin(tmp_path, "b", ["a"])
    plugins = {p.plugin_id: p for p in discover_plugins([str(tmp_path)])}
    assert not plugins["a"].valid
    assert not plugins["b"].valid
    assert "cycle" in (plugins["a"].error or "").lower()
    assert "cycle" in (plugins["b"].error or "").lower()


def test_a_three_plugin_cycle_invalidates_all_three(tmp_path: Path) -> None:
    _plugin(tmp_path, "a", ["b"])
    _plugin(tmp_path, "b", ["c"])
    _plugin(tmp_path, "c", ["a"])
    plugins = {p.plugin_id: p for p in discover_plugins([str(tmp_path)])}
    assert not plugins["a"].valid
    assert not plugins["b"].valid
    assert not plugins["c"].valid


def test_depending_on_a_plugin_absent_from_disk_is_not_a_discovery_error(tmp_path: Path) -> None:
    # Discovery only detects CYCLES among what it can see; a dependency that
    # simply does not exist on disk is an install-time error (Step 4 below),
    # not a discovery-time one -- discovery has no way to distinguish "not
    # installed yet" from "never existed" without also knowing the tenant's
    # install state, which is a DB concern outside discovery's scope.
    _plugin(tmp_path, "a", ["nonexistent"])
    [discovered] = discover_plugins([str(tmp_path)])
    assert discovered.valid
