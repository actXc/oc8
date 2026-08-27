from __future__ import annotations

from pathlib import Path

import pytest

from oc8.capas.discovery import _read_guardrails_folder
from oc8.capas.manifest import ManifestError


def _write(folder: Path, name: str, content: str) -> None:
    (folder / name).write_text(content, encoding="utf-8")


def test_reads_a_preset_file_into_the_named_connection(tmp_path: Path) -> None:
    guardrails = tmp_path / "guardrails"
    guardrails.mkdir()
    _write(
        guardrails,
        "read_only.toml",
        """
kind = "preset"
connection = "primary"
key = "read_only"
label = "Nur lesen"
label_en = "Read only"
summary = "Kann lesen."
summary_en = "Can read."
read = true
""",
    )
    presets_by_connection, library = _read_guardrails_folder(tmp_path)
    assert list(presets_by_connection.keys()) == ["primary"]
    assert len(presets_by_connection["primary"]) == 1
    assert presets_by_connection["primary"][0].key == "read_only"
    assert presets_by_connection["primary"][0].read is True
    assert library is None


def test_an_omitted_connection_is_reported_as_none_not_the_string_primary(
    tmp_path: Path,
) -> None:
    # The spec binds a `connection`-less preset to the plugin's SOLE
    # tool_pack connection whatever its key is -- `ToolPackConnection.key`'s
    # own model default is "default", not "primary", so defaulting to the
    # literal string "primary" here would hand a community plugin a bogus
    # "unknown tool_pack connection" error for well-formed content. This
    # reader reports "unbound"; Task 4 resolves it against the real
    # connection list, which is the only layer that knows it.
    guardrails = tmp_path / "guardrails"
    guardrails.mkdir()
    _write(
        guardrails,
        "read_only.toml",
        """
kind = "preset"
key = "read_only"
label = "Nur lesen"
label_en = "Read only"
summary = "Kann lesen."
summary_en = "Can read."
read = true
""",
    )
    presets_by_connection, library = _read_guardrails_folder(tmp_path)
    assert list(presets_by_connection.keys()) == [None]
    assert library is None


def test_reads_a_library_file_into_the_guardrail_library(tmp_path: Path) -> None:
    guardrails = tmp_path / "guardrails"
    guardrails.mkdir()
    _write(
        guardrails,
        "mail_assistant_drafts_only.toml",
        """
kind = "library"
key = "mail_assistant_drafts_only"
label = "Nur Entwürfe"
label_en = "Drafts only"
summary = "Erstellt nur Entwürfe."
summary_en = "Creates only drafts."
use_case = "mail"
""",
    )
    presets_by_connection, library = _read_guardrails_folder(tmp_path)
    assert presets_by_connection == {}
    assert library is not None
    assert len(library.guardrail) == 1
    assert library.guardrail[0].key == "mail_assistant_drafts_only"
    assert library.guardrail[0].use_case == "mail"


def test_missing_guardrails_folder_returns_no_library(tmp_path: Path) -> None:
    presets_by_connection, library = _read_guardrails_folder(tmp_path)
    assert presets_by_connection == {}
    # `None`, NOT GuardrailLibrary(guardrail=[]) -- see this task's Interfaces
    # note: an empty object flips a public API field from null to [].
    assert library is None


def test_filename_must_equal_key(tmp_path: Path) -> None:
    guardrails = tmp_path / "guardrails"
    guardrails.mkdir()
    _write(
        guardrails,
        "read_only.toml",
        """
kind = "library"
key = "not_read_only"
label = "x"
label_en = "x"
summary = "x"
summary_en = "x"
use_case = "x"
""",
    )
    with pytest.raises(ManifestError, match=r"read_only.*not_read_only|not_read_only.*read_only"):
        _read_guardrails_folder(tmp_path)


def test_kind_is_required(tmp_path: Path) -> None:
    guardrails = tmp_path / "guardrails"
    guardrails.mkdir()
    _write(guardrails, "x.toml", 'key = "x"\n')
    with pytest.raises(ManifestError, match="kind"):
        _read_guardrails_folder(tmp_path)


def test_a_non_toml_file_in_guardrails_is_a_hard_error(tmp_path: Path) -> None:
    # `read_only.tml` matches no `*.toml` glob, so the guardrail did not exist
    # -- a silently missing permission ceiling. Rejected by name instead.
    guardrails = tmp_path / "guardrails"
    guardrails.mkdir()
    _write(
        guardrails,
        "read_only.tml",
        """
kind = "preset"
key = "read_only"
label = "x"
label_en = "x"
summary = "x"
summary_en = "x"
read = true
""",
    )
    with pytest.raises(ManifestError, match=r"read_only\.tml"):
        _read_guardrails_folder(tmp_path)
