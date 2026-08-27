from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from oc8.capas.loader import import_entry_point


def _write_connector_package(folder: Path, marker: str) -> None:
    pkg = folder / "connector"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "connector.py").write_text(
        f'MARKER = "{marker}"\n\n\ndef register(contrib):\n    contrib.append(MARKER)\n',
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _clean_sys_modules() -> Iterator[None]:
    before = set(sys.modules.keys())
    yield
    for name in set(sys.modules.keys()) - before:
        del sys.modules[name]


def test_two_plugins_with_the_same_folder_name_do_not_collide(tmp_path: Path) -> None:
    plugin_a = tmp_path / "plugin_a"
    plugin_a.mkdir()
    _write_connector_package(plugin_a, "A")

    plugin_b = tmp_path / "plugin_b"
    plugin_b.mkdir()
    _write_connector_package(plugin_b, "B")

    # Assert on the RETURNED CALLABLES -- what `load_plugin` actually gets --
    # never by re-importing `connector.connector` in the test. The fix itself
    # removes the folder from sys.path AND evicts every `connector*` key from
    # sys.modules before returning, so a test-level `import connector...`
    # raises ModuleNotFoundError: it would look like the fix is wrong and
    # invite an implementer to weaken it. The function object stays perfectly
    # usable after its module is unregistered, which is the whole point.
    # `import_entry_point` is typed `-> object` (loader.py:54) -- deliberately,
    # since it can return any plugin callable; `load_plugin` itself carries a
    # `# type: ignore[operator]` at line 123 for the same reason. Cast once,
    # here, rather than sprinkling ignores: `files = ["src", "tests"]` +
    # `strict = true` means this test file IS type-checked at full strictness.
    register_a = cast(Any, import_entry_point(str(plugin_a), "connector.connector:register"))
    register_b = cast(Any, import_entry_point(str(plugin_b), "connector.connector:register"))

    assert register_a.__globals__["MARKER"] == "A"
    assert register_b.__globals__["MARKER"] == "B", (
        "plugin B's import returned plugin A's cached 'connector' module -- "
        "sys.modules was not cleaned up between plugin loads"
    )
    assert register_a is not register_b

    # And the same fact via the real calling convention: each register() must
    # contribute its OWN marker (spec §8's own wording).
    contributed: list[str] = []
    register_a(contributed)
    register_b(contributed)
    assert contributed == ["A", "B"]


def test_target_folder_wins_over_an_ambient_earlier_sys_path_entry(tmp_path: Path) -> None:
    # The other half of the collision defence: eviction alone is not enough if
    # resolution still depends on ambient sys.path ORDER. With both plugin
    # roots already on sys.path and the REQUESTED one not first, skipping the
    # insert (because "the folder is already there") resolved
    # `connector.connector` against the OTHER plugin and returned a
    # correct-looking `register` for the wrong code.
    plugin_a = tmp_path / "plugin_a"
    plugin_a.mkdir()
    _write_connector_package(plugin_a, "A")

    plugin_b = tmp_path / "plugin_b"
    plugin_b.mkdir()
    _write_connector_package(plugin_b, "B")

    sys.path.insert(0, str(plugin_b))
    sys.path.insert(0, str(plugin_a))  # A is now ahead of B
    try:
        register_b = cast(Any, import_entry_point(str(plugin_b), "connector.connector:register"))
        assert register_b.__globals__["MARKER"] == "B", (
            "asked for plugin B's connector and got plugin A's -- import_entry_point "
            "resolved against ambient sys.path order instead of the folder it was given"
        )
        assert register_b.__globals__["__file__"] == str(plugin_b / "connector" / "connector.py")
        # Its own temporary insertion is removed; the caller's pre-existing
        # entries are left exactly as they were.
        assert sys.path.count(str(plugin_b)) == 1
        assert sys.path.count(str(plugin_a)) == 1
        assert sys.path.index(str(plugin_a)) < sys.path.index(str(plugin_b))
    finally:
        sys.path.remove(str(plugin_a))
        sys.path.remove(str(plugin_b))


def test_sys_path_does_not_accumulate_plugin_folders(tmp_path: Path) -> None:
    plugin_a = tmp_path / "plugin_a"
    plugin_a.mkdir()
    _write_connector_package(plugin_a, "A")

    import_entry_point(str(plugin_a), "connector.connector:register")

    assert str(plugin_a) not in sys.path
