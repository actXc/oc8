from __future__ import annotations

from pathlib import Path

import pytest

from oc8.capas.guardrails import parse_guardrails_toml
from oc8.capas.manifest import ManifestError

_VALID = """
[[guardrail]]
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

  [[guardrail.adjustable]]
  field = "approval_eur"
  label = "Freigabe ab"
  label_en = "Approval from"
  unit = "EUR"
  min = 0
  max = 10000
"""


def _write(tmp_path: Path, body: str, name: str = "guardrails.toml") -> Path:
    p = tmp_path / name
    p.write_text(body)
    return p


def test_parses_a_minimal_valid_file(tmp_path: Path) -> None:
    path = _write(tmp_path, _VALID)
    lib = parse_guardrails_toml(path)
    assert lib is not None
    assert len(lib.guardrail) == 1
    g = lib.guardrail[0]
    assert g.key == "quote_approval_threshold"
    assert g.label == "Angebote ab einem Betrag freigeben"
    assert g.label_en == "Approve quotes above an amount"
    assert g.use_case == "sales"
    assert g.read is True
    assert g.write is False
    assert g.send is True
    assert g.approval_eur == 3000
    assert g.approval_actions == frozenset()
    assert g.only == ()
    assert len(g.adjustable) == 1
    adj = g.adjustable[0]
    assert adj.field == "approval_eur"
    assert adj.unit == "EUR"
    assert adj.min == 0
    assert adj.max == 10000


def test_missing_file_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "does_not_exist.toml"
    assert parse_guardrails_toml(path) is None


def test_duplicate_key_raises_manifest_error_naming_the_file(tmp_path: Path) -> None:
    body = _VALID + _VALID  # same key = "quote_approval_threshold" twice
    path = _write(tmp_path, body)
    with pytest.raises(ManifestError) as exc:
        parse_guardrails_toml(path)
    assert str(path) in str(exc.value)


def test_approval_actions_accepts_tool_names_not_only_rights(tmp_path: Path) -> None:
    # A tool-key entry (not a RIGHTS value) must be accepted: approval_actions
    # is not restricted to RIGHTS for the new Guardrail model (unlike the
    # older GuardrailPreset), since Task 1's PDP change lets it gate a tool by
    # name.
    body = _VALID.replace("approval_actions = []", 'approval_actions = ["post_message"]')
    path = _write(tmp_path, body)
    lib = parse_guardrails_toml(path)
    assert lib is not None
    assert lib.guardrail[0].approval_actions == frozenset({"post_message"})


def test_approval_actions_rejects_empty_string_entries(tmp_path: Path) -> None:
    body = _VALID.replace("approval_actions = []", 'approval_actions = ["", "send"]')
    path = _write(tmp_path, body)
    with pytest.raises(ManifestError):
        parse_guardrails_toml(path)


def test_approval_eur_zero_is_preserved_not_normalised_to_none(tmp_path: Path) -> None:
    body = _VALID.replace("approval_eur = 3000", "approval_eur = 0").replace(
        "min = 0\n  max = 10000", "min = 0\n  max = 0"
    )
    path = _write(tmp_path, body)
    lib = parse_guardrails_toml(path)
    assert lib is not None
    assert lib.guardrail[0].approval_eur == 0


def test_approval_eur_empty_string_normalises_to_none(tmp_path: Path) -> None:
    body = _VALID.replace("approval_eur = 3000", 'approval_eur = ""').replace(
        '  [[guardrail.adjustable]]\n  field = "approval_eur"\n  label = "Freigabe ab"\n'
        '  label_en = "Approval from"\n  unit = "EUR"\n  min = 0\n  max = 10000\n',
        "",
    )
    path = _write(tmp_path, body)
    lib = parse_guardrails_toml(path)
    assert lib is not None
    assert lib.guardrail[0].approval_eur is None
    assert lib.guardrail[0].adjustable == []


def test_adjustable_field_naming_a_nonexistent_field_raises(tmp_path: Path) -> None:
    body = _VALID.replace('field = "approval_eur"', 'field = "bogus"')
    path = _write(tmp_path, body)
    with pytest.raises(ManifestError):
        parse_guardrails_toml(path)


def test_adjustable_min_max_must_bracket_the_shipped_default(tmp_path: Path) -> None:
    # approval_eur = 3000 but the adjustable range is [5000, 6000] -- the
    # shipped default falls outside its own declared bounds.
    body = _VALID.replace("min = 0\n  max = 10000", "min = 5000\n  max = 6000")
    path = _write(tmp_path, body)
    with pytest.raises(ManifestError):
        parse_guardrails_toml(path)


def test_extra_forbid_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    body = _VALID.replace('use_case = "sales"', 'use_case = "sales"\nbogus_field = "x"')
    path = _write(tmp_path, body)
    with pytest.raises(ManifestError):
        parse_guardrails_toml(path)
