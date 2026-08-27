"""Tests for the skills/*.toml folder-discovery convention (design §Part 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from oc8.capas.discovery import _read, _read_skills_folder
from oc8.capas.manifest import ManifestError


def _write_plugin_toml(folder: Path, *, extra: str = "") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.toml").write_text(
        f"""
[plugin]
name = "{folder.name}"
version = "1.0.0"
type = "skill"
trust = "first_party"
summary = "test plugin"
{extra}
""".strip()
        + "\n"
    )


def test_read_skills_folder_returns_empty_list_when_folder_missing(tmp_path: Path) -> None:
    assert _read_skills_folder(tmp_path) == []


def test_read_skills_folder_reads_one_file(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "greet.toml").write_text(
        """
name = "Greet"
category = "support"
description = "Say hello."
instruction = "Say hello to the user."
requires_tools = ["odoo"]
guardrails = ["Never be rude."]
""".strip()
        + "\n"
    )
    specs = _read_skills_folder(tmp_path)
    assert len(specs) == 1
    assert specs[0].name == "Greet"
    assert specs[0].requires_tools == ["odoo"]
    assert specs[0].guardrails == ["Never be rude."]


def test_read_skills_folder_reads_multiple_files_sorted_by_filename(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "b-second.toml").write_text('name = "Second"\n')
    (skills_dir / "a-first.toml").write_text('name = "First"\n')
    specs = _read_skills_folder(tmp_path)
    assert [s.name for s in specs] == ["First", "Second"]


def test_read_skills_folder_rejects_non_toml_file(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "greet.toml").write_text('name = "Greet"\n')
    (skills_dir / "notes.md").write_text("not a skill file\n")
    with pytest.raises(ManifestError, match=r"notes\.md"):
        _read_skills_folder(tmp_path)


def test_read_skills_folder_rejects_malformed_skill_file(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "bad.toml").write_text('unknown_field = "oops"\n')
    with pytest.raises(ManifestError, match=r"bad\.toml"):
        _read_skills_folder(tmp_path)


def test_discovered_plugin_has_skill_pack_from_skills_folder(tmp_path: Path) -> None:
    folder = tmp_path / "greeter_skill"
    _write_plugin_toml(folder)
    skills_dir = folder / "skills"
    skills_dir.mkdir()
    (skills_dir / "greet.toml").write_text(
        """
name = "Greet"
description = "Say hello."
instruction = "Say hello to the user."
""".strip()
        + "\n"
    )
    discovered = _read(folder)
    assert discovered.valid, discovered.error
    assert discovered.manifest is not None
    skill_pack = discovered.manifest["skill_pack"]
    assert skill_pack is not None
    assert len(skill_pack["skills"]) == 1
    assert skill_pack["skills"][0]["name"] == "Greet"


def test_inline_skill_pack_in_plugin_toml_is_hard_cutover_rejected(tmp_path: Path) -> None:
    folder = tmp_path / "old_style_skill"
    extra = """
[plugin.skill_pack]

[[plugin.skill_pack.skills]]
name = "Old"
description = "old inline style"
""".strip()
    _write_plugin_toml(folder, extra=extra)
    discovered = _read(folder)
    assert not discovered.valid
    assert "skills/<slug>.toml" in (discovered.error or "")


def test_inline_skill_template_in_plugin_toml_is_hard_cutover_rejected(tmp_path: Path) -> None:
    folder = tmp_path / "old_style_template"
    extra = """
[plugin.skill_template]
name = "Old"
description = "old inline style"
""".strip()
    _write_plugin_toml(folder, extra=extra)
    discovered = _read(folder)
    assert not discovered.valid
    assert "skills/<slug>.toml" in (discovered.error or "")
