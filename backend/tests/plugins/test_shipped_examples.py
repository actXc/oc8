"""The plugin folders we ship must actually parse. A broken example is worse
than no example -- it is the first thing a plugin author copies."""

from __future__ import annotations

import re
from pathlib import Path

from oc8.capas.discovery import discover_plugins
from oc8.capas.manifest import parse_manifest

# tests/plugins/<this> -> tests -> backend -> repo root, where capas/ lives.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGINS_DIR = _REPO_ROOT / "capas"
_BACKEND_TESTS_DIR = _REPO_ROOT / "backend" / "tests"


def test_the_plugins_directory_exists() -> None:
    assert _PLUGINS_DIR.is_dir(), f"{_PLUGINS_DIR} missing"


def test_shipped_example_plugins_are_all_valid() -> None:
    found = discover_plugins([str(_PLUGINS_DIR)])
    assert found, "no example plugins found"
    for p in found:
        assert p.valid, f"{p.plugin_id}: {p.error}"


def test_every_setup_form_that_creates_a_connection_can_bind_it_to_a_department() -> None:
    """A repo-wide guard, not a microsoft365 one.

    `runtime/executor.py` selects a tool connection with
    `McpConnection.department_id == agent.department_id`. `materialise.py`
    creates the row with no department at all, and the ONLY thing that ever
    sets one is `configure_plugin` reading `setup.mcp.department_field`. A
    manifest that declares `[plugin.setup.mcp]` without it therefore ships a
    plugin whose setup form succeeds, whose OAuth/credentials are green, and
    whose connection no agent in any department will ever be handed -- silently,
    forever, with `NULL = <uuid>` quietly false in SQL. microsoft365 shipped
    exactly that (whole-branch review, C4); odoo_mcp and gitea_mcp did not.
    This is what stops the next tool pack repeating it.
    """
    offenders: list[str] = []
    for found in discover_plugins([str(_PLUGINS_DIR)]):
        if found.manifest is None:
            continue
        setup = parse_manifest(found.manifest).setup
        if setup is None or setup.mcp is None:
            continue
        if not setup.mcp.department_field:
            offenders.append(found.plugin_id)
            continue
        keys = {field.key for field in setup.fields if field.kind == "department"}
        if setup.mcp.department_field not in keys:
            offenders.append(
                f"{found.plugin_id} (department_field="
                f"{setup.mcp.department_field!r} names no kind='department' field)"
            )
    assert not offenders, (
        "these manifests create an McpConnection no agent can ever be given: "
        + ", ".join(offenders)
    )


def test_no_guardrail_preset_restricts_the_agent_to_a_tool_that_does_not_exist() -> None:
    """A typo in an `only` list is silent: `authz/pdp.py` intersects it with the
    real tool names, so a misspelt entry simply removes a capability the preset
    promised. Checked against the connection's OWN declared scopes, which the
    per-plugin manifest tests already prove match the real tool modules."""
    for found in discover_plugins([str(_PLUGINS_DIR)]):
        if found.manifest is None:
            continue
        tool_pack = parse_manifest(found.manifest).tool_pack
        if tool_pack is None:
            continue
        for conn in tool_pack.connections:
            scopes = conn.scopes
            if not isinstance(scopes, dict):
                continue
            declared = {name for names in scopes.values() for name in names}
            if not declared:
                continue
            for preset in conn.guardrail_presets:
                for name in preset.only or []:
                    assert name in declared, (
                        f"{found.plugin_id}/{conn.key} preset {preset.key!r} "
                        f"names unknown tool {name!r}"
                    )


def test_no_plugin_local_tests_folder_declares_an_init_py() -> None:
    """A repo-wide LAYOUT guard, and the reason it exists is worth reading.

    Plugin-local tests live at `plugins/<name>/tests/` (design §2). pytest runs
    them under `--import-mode=importlib` with `consider_namespace_packages =
    true` (see backend/pyproject.toml). Under those settings an `__init__.py`
    in that folder makes pytest name each module as a member of the TOP-LEVEL
    `tests` package -- which is already claimed by `backend/tests/__init__.py`
    -- keyed by BASENAME ALONE. Two plugins shipping a `test_manifest.py` then
    resolve to the same module name, and pytest silently re-collects the FIRST
    file's tests under the SECOND file's node IDs: the second file's tests
    never run.

    That failure is green, and the test COUNT GOES UP, which is why nobody
    notices. Measured on this repo: microsoft365's 60 plugin-local tests plus a
    second plugin shipping four same-named files reported `92 passed` with
    `__init__.py` present and ZERO of the second plugin's tests actually
    collected; without it, `64 passed` and all four collected.

    Without the `__init__.py`, pytest derives a unique PATH-based module name
    per file, so duplicate basenames across plugins are perfectly safe -- and
    they are expected, since every retrofitted plugin ships `test_manifest.py`.
    Do NOT "fix" a future collision by renaming test files; the rule is this
    one, and it is one `rm` per plugin.
    """
    offenders = sorted(str(p) for p in _PLUGINS_DIR.glob("*/tests/__init__.py"))
    assert not offenders, (
        "plugin-local tests/ folders must NOT be packages -- an __init__.py "
        "grafts them into backend/tests' namespace by basename and silently "
        "swallows another plugin's same-named test file: " + ", ".join(offenders)
    )


def test_every_plugin_local_test_file_evicts_its_generic_package_symmetrically() -> None:
    """A repo-wide guard on the `sys.modules` eviction convention (design §5.2).

    After the restructure every plugin's package is named for its
    RESPONSIBILITY (`connector`, `runtime`, `mcp_bridge`, ...), so several
    plugins share a top-level module name and `sys.modules` -- keyed by name,
    not path -- hands whichever landed first to everyone else. A plugin-local
    test that puts its own root on `sys.path` must therefore evict those names
    on BOTH sides of its import: before, so a sibling's cached copy cannot
    answer it, and after, so nothing generic is left behind for
    `loader.import_entry_point` to reuse (that function only evicts modules IT
    ITSELF introduced, so a pre-cached name makes it return the WRONG plugin's
    `register` -- reproduced live during Task 12).

    This guard is static, deliberately: the trailing eviction's ABSENCE is not
    observable from a green test run. A prelude runs at collection time, and
    any later file's own eviction (or an autouse fixture's teardown) masks an
    earlier file's missing one. So a plugin author cannot confirm they copied
    the pattern correctly by running the suite -- only by being told, which is
    what this test does.

    Scoped to `plugins/*/tests/*.py` (every file there puts its own plugin
    root on `sys.path`, so all are in scope) AND `backend/tests/**/*.py`
    files that import one of the GENERIC package names as a bare top-level
    module (`connector`, `mcp_bridge`, `channel`, `runtime`, `provider`) --
    not just the former (Task 13 review finding): `backend/tests/agents/
    test_mcp_env.py` also puts a plugin root on `sys.path` and needs the same
    symmetric eviction, and Tasks 14-16 retrofit several more plugins whose
    existing `backend/tests/` suites do the same. A `backend/tests/` file is
    excluded until its plugin is actually retrofitted: pre-retrofit, those
    files still import a plugin-SPECIFIC unique name (`nanoclaw_runtime`,
    `claude_code_runtime`, `telegram_approvals`, ...), which cannot collide
    and does not need eviction -- checking every `sys.path.insert(` in
    `backend/tests/` unconditionally would flag ~15 legitimate pre-retrofit
    files. The eviction helper's name varies (`_evict()`,
    `_evict_generic_plugin_packages()`, ...), so calls are matched by the
    `_evict...()` prefix rather than the literal string, excluding the
    `def _evict...():` definition itself.
    """
    _GENERIC_NAMES = ("connector", "mcp_bridge", "channel", "runtime", "provider")
    _generic_import_re = re.compile(
        r"^\s*(?:from|import)\s+(" + "|".join(_GENERIC_NAMES) + r")\b", re.MULTILINE
    )
    offenders: list[str] = []
    plugin_local = sorted(_PLUGINS_DIR.glob("*/tests/test_*.py"))
    backend_side = [
        p
        for p in sorted(_BACKEND_TESTS_DIR.glob("**/test_*.py"))
        if _generic_import_re.search(p.read_text(encoding="utf-8"))
    ]
    for path in plugin_local + backend_side:
        source = path.read_text(encoding="utf-8")
        inserts = source.count("sys.path.insert(")
        if not inserts:
            continue  # imports nothing from its own plugin; nothing to evict
        removes = source.count("sys.path.remove(")
        # One call before the import/yield and one after -- the definition of
        # symmetric. `(?<!def )` excludes the `def _evict...():` line itself.
        evictions = len(re.findall(r"(?<!def )\b_evict\w*\(\)", source))
        rel = path.relative_to(_REPO_ROOT).as_posix()
        if removes != inserts:
            offenders.append(f"{rel}: {inserts} sys.path.insert but {removes} sys.path.remove")
        if evictions < 2 * inserts:
            offenders.append(
                f"{rel}: {evictions} _evict...() call(s) for {inserts} sys.path.insert -- "
                "eviction must happen on BOTH sides of the import (or of the fixture's yield)"
            )
    assert not offenders, "asymmetric sys.modules eviction: " + "; ".join(offenders)


def test_the_sales_bundle_example_is_a_usable_department_template() -> None:
    found = {p.plugin_id: p for p in discover_plugins([str(_PLUGINS_DIR)])}
    p = found["vertrieb_bundle"]
    assert p.type == "department_template"
    assert p.summary
    assert p.manifest is not None
    spec = p.manifest["department_template"]

    agents = spec["agents"]
    assert agents, "the bundle should ship agents"
    assert sum(1 for a in agents if a["is_team_lead"]) == 1, "exactly one team lead"

    names = {a["name"] for a in agents}
    for a in agents:
        if a["reports_to"] is not None:
            assert a["reports_to"] in names, f"dangling reports_to: {a['reports_to']}"

    # An empty `memory` frame denies department/company memory outright
    # (oc8.memory.policy). The example must not ship that trap.
    assert "write" in spec["frame"]["memory"]["department"]
