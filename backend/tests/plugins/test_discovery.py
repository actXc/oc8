from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from oc8.capas.discovery import (
    GUARDRAILS_DIRNAME,
    MANIFEST_FILENAME,
    discover_plugins,
    find_plugin,
    invalidate_discovery_cache,
)


@pytest.fixture(autouse=True)
def _clear_discovery_cache() -> Iterator[None]:
    """Every test below uses its own fresh `tmp_path`, so the TTL cache added
    for the Capas-list-page perf fix would naturally miss regardless -- this
    is just hygiene so the module-global cache dict doesn't accumulate dead
    entries for since-deleted tmp_path roots across a long test session, and
    so `test_repeated_calls_within_the_ttl_are_cached` below starts clean
    irrespective of test execution order."""
    invalidate_discovery_cache()
    yield
    invalidate_discovery_cache()

_VALID = """
[plugin]
name = "acme_bundle"
version = "1.0.0"
type = "department_template"
trust = "first_party"
summary = "An example bundle"
"""

#: One `guardrails/<key>.toml` entry (design §3-4). The file's own `key` must
#: match its filename -- `_write_guardrails` below enforces that by deriving
#: the filename from the `key` argument, not from this template.
_VALID_GUARDRAIL_ENTRY = """
kind = "library"
key = "quote_approval_threshold"
label = "Angebote ab einem Betrag freigeben"
label_en = "Approve quotes above an amount"
summary = "Erstellt Angebote selbststaendig; ab dem Betrag entscheidet ein Mensch."
summary_en = "Creates quotes on its own; above the amount a human decides."
use_case = "sales"
read = true
write = false
send = true
approval_eur = 3000
approval_actions = []
only = []
"""


def _write(root: Path, folder: str, body: str) -> None:
    d = root / folder
    d.mkdir(parents=True)
    (d / MANIFEST_FILENAME).write_text(body)


def _write_guardrails(root: Path, folder: str, key: str, body: str) -> None:
    d = root / folder / GUARDRAILS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.toml").write_text(body)


def test_discovers_a_valid_plugin(tmp_path: Path) -> None:
    _write(tmp_path, "acme_bundle", _VALID)
    found = discover_plugins([str(tmp_path)])
    assert len(found) == 1
    p = found[0]
    assert p.plugin_id == "acme_bundle"
    assert p.valid is True
    assert p.error is None
    assert p.name == "acme_bundle"
    assert p.version == "1.0.0"
    assert p.type == "department_template"
    assert p.summary == "An example bundle"
    assert p.manifest is not None and p.manifest["name"] == "acme_bundle"


def test_folder_without_a_manifest_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "not_a_plugin").mkdir()
    assert discover_plugins([str(tmp_path)]) == []


def test_a_loose_file_in_the_root_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("not a plugin")
    assert discover_plugins([str(tmp_path)]) == []


def test_malformed_toml_is_reported_not_raised(tmp_path: Path) -> None:
    _write(tmp_path, "broken", "[plugin\nname = ")
    found = discover_plugins([str(tmp_path)])
    assert len(found) == 1
    assert found[0].valid is False
    assert found[0].error


def test_unknown_key_is_invalid_because_schema_forbids_extra(tmp_path: Path) -> None:
    _write(tmp_path, "typo", _VALID + '\nnonsense_key = "x"\n')
    found = discover_plugins([str(tmp_path)])
    assert found[0].valid is False
    assert found[0].error


def test_folder_name_must_match_manifest_name(tmp_path: Path) -> None:
    _write(tmp_path, "wrong_folder", _VALID)  # manifest says acme_bundle
    found = discover_plugins([str(tmp_path)])
    assert found[0].valid is False
    assert "folder" in (found[0].error or "").lower()


def test_missing_plugin_table_is_invalid(tmp_path: Path) -> None:
    _write(tmp_path, "empty", "[other]\nx = 1\n")
    found = discover_plugins([str(tmp_path)])
    assert found[0].valid is False


def test_missing_required_field_is_invalid(tmp_path: Path) -> None:
    _write(tmp_path, "noversion", '[plugin]\nname = "noversion"\n')
    found = discover_plugins([str(tmp_path)])
    assert found[0].valid is False
    assert found[0].error


def test_multiple_roots_are_scanned_and_first_wins(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _write(a, "dupe", _VALID.replace("acme_bundle", "dupe"))
    _write(b, "dupe", _VALID.replace("acme_bundle", "dupe").replace("1.0.0", "2.0.0"))
    found = discover_plugins([str(a), str(b)])
    ids = [p.plugin_id for p in found]
    assert ids.count("dupe") == 1
    assert next(p for p in found if p.plugin_id == "dupe").version == "1.0.0"


def test_missing_root_is_not_an_error(tmp_path: Path) -> None:
    assert discover_plugins([str(tmp_path / "does_not_exist")]) == []


def test_find_plugin_by_id(tmp_path: Path) -> None:
    _write(tmp_path, "acme_bundle", _VALID)
    assert find_plugin("acme_bundle", [str(tmp_path)]) is not None
    assert find_plugin("nope", [str(tmp_path)]) is None


def test_results_are_sorted_by_id(tmp_path: Path) -> None:
    _write(tmp_path, "zeta", _VALID.replace("acme_bundle", "zeta"))
    _write(tmp_path, "alpha", _VALID.replace("acme_bundle", "alpha"))
    assert [p.plugin_id for p in discover_plugins([str(tmp_path)])] == ["alpha", "zeta"]


def test_nested_agents_department_layout_is_discovered(tmp_path: Path) -> None:
    """Store catalog layout: capas/agents/<dept>/<id>/plugin.toml."""
    nested = tmp_path / "agents" / "finance" / "finance_bookkeeper"
    nested.mkdir(parents=True)
    (nested / MANIFEST_FILENAME).write_text(
        _VALID.replace("acme_bundle", "finance_bookkeeper"),
        encoding="utf-8",
    )
    # Organizer dirs must not be treated as capas.
    assert not (tmp_path / "agents" / MANIFEST_FILENAME).exists()
    found = discover_plugins([str(tmp_path)])
    assert len(found) == 1
    assert found[0].plugin_id == "finance_bookkeeper"
    assert found[0].valid is True
    assert found[0].path.endswith("agents/finance/finance_bookkeeper")


def test_discovery_never_imports_plugin_code(tmp_path: Path) -> None:
    """Discovery reads a TOML file and nothing else. A plugin folder that would
    raise on import must still be discovered cleanly -- code loading is slice 2."""
    _write(tmp_path, "acme_bundle", _VALID)
    pkg = tmp_path / "acme_bundle" / "acme_bundle"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("raise RuntimeError('must never be imported')")
    found = discover_plugins([str(tmp_path)])
    assert found[0].valid is True


def test_plugin_without_a_guardrails_folder_has_no_library(tmp_path: Path) -> None:
    _write(tmp_path, "acme_bundle", _VALID)
    found = discover_plugins([str(tmp_path)])
    assert found[0].valid is True
    assert found[0].error is None
    assert found[0].guardrail_library is None


def test_plugin_with_valid_guardrails_toml_populates_library(tmp_path: Path) -> None:
    _write(tmp_path, "acme_bundle", _VALID)
    _write_guardrails(tmp_path, "acme_bundle", "quote_approval_threshold", _VALID_GUARDRAIL_ENTRY)
    found = discover_plugins([str(tmp_path)])
    assert found[0].valid is True
    assert found[0].error is None
    lib = found[0].guardrail_library
    assert lib is not None
    assert len(lib.guardrail) == 1
    assert lib.guardrail[0].key == "quote_approval_threshold"


def test_malformed_guardrails_toml_fails_only_that_plugin(tmp_path: Path) -> None:
    _write(tmp_path, "broken_guardrails", _VALID.replace("acme_bundle", "broken_guardrails"))
    # Filename declares "quote_approval_threshold" but the entry's own `key`
    # disagrees -- a realistic copy-paste mistake, caught before the entry
    # ever reaches pydantic validation.
    _write_guardrails(
        tmp_path,
        "broken_guardrails",
        "quote_approval_threshold",
        _VALID_GUARDRAIL_ENTRY.replace(
            'key = "quote_approval_threshold"', 'key = "renamed_by_mistake"'
        ),
    )
    _write(tmp_path, "healthy_sibling", _VALID.replace("acme_bundle", "healthy_sibling"))

    found = discover_plugins([str(tmp_path)])
    assert len(found) == 2

    broken = next(p for p in found if p.plugin_id == "broken_guardrails")
    assert broken.valid is False
    assert broken.error is not None
    assert f"{GUARDRAILS_DIRNAME}/" in broken.error

    healthy = next(p for p in found if p.plugin_id == "healthy_sibling")
    assert healthy.valid is True
    assert healthy.error is None


def test_a_flat_guardrails_toml_is_invalid_and_names_the_new_layout(tmp_path: Path) -> None:
    """The loud-failure guard from Task 4: a plugin still carrying the OLD flat
    `guardrails.toml` at its root is rejected, not silently ignored, and the
    error names the new `guardrails/` location."""
    _write(tmp_path, "flat_guardrails", _VALID.replace("acme_bundle", "flat_guardrails"))
    (tmp_path / "flat_guardrails" / "guardrails.toml").write_text("[[guardrail]]\n")
    found = discover_plugins([str(tmp_path)])
    assert found[0].valid is False
    assert f"{GUARDRAILS_DIRNAME}/" in (found[0].error or "")


def test_repeated_calls_within_the_ttl_are_cached(tmp_path: Path) -> None:
    """The Capas list page fires one `GET /capas/{id}/icon` per row, each of
    which calls `find_plugin` -> `discover_plugins`: without caching, N rows
    means N full uncached rescans in quick succession. Prove the cache is
    actually hit (a plugin dropped after the first call is invisible to an
    immediate second call on the same root), then prove
    `invalidate_discovery_cache()` forces a genuinely fresh scan."""
    _write(tmp_path, "acme_bundle", _VALID)
    first = discover_plugins([str(tmp_path)])
    assert len(first) == 1

    _write(tmp_path, "another_bundle", _VALID.replace("acme_bundle", "another_bundle"))
    second = discover_plugins([str(tmp_path)])
    assert second == first, "a cache hit must return the pre-write result unchanged"

    invalidate_discovery_cache()
    third = discover_plugins([str(tmp_path)])
    assert {p.plugin_id for p in third} == {"acme_bundle", "another_bundle"}
