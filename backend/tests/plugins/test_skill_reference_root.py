"""`reference_root` traces a skill back to the on-disk directory (relative to
its capa) its own references/assets/scripts live under -- so that
agent/control_tools.py's read_reference_file can serve them at runtime.
capas.manifest.SkillTemplateSpec.reference_root's docstring is the source of
truth for the three cases exercised here."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.capas.claude_adapter import adapt_claude_plugin
from oc8.capas.discovery import _read, _read_skills_folder
from oc8.capas.manifest import Manifest, SkillTemplateSpec
from oc8.capas.materialise import _materialise_one_skill
from tests.conftest import AppSessionFactory


def _write_plugin_toml(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.toml").write_text(
        f"""
[plugin]
name = "{folder.name}"
version = "1.0.0"
type = "skill"
trust = "first_party"
summary = "test plugin"
""".strip()
        + "\n"
    )


def test_toml_skill_has_no_reference_root(tmp_path: Path) -> None:
    """A skills/*.toml native skill has no on-disk directory of its own --
    the whole skill IS one file, not a directory with siblings."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "greet.toml").write_text(
        'name = "Greet"\ninstruction = "Say hello."\n'
    )
    specs = _read_skills_folder(tmp_path)
    assert len(specs) == 1
    assert specs[0].reference_root is None


def test_skill_md_subdirectory_reference_root_is_relative_to_capa_root(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills" / "thai-compliance"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: Thai Compliance\n---\n\nSee references/checklist.md."
    )
    (skills_dir / "references").mkdir()
    (skills_dir / "references" / "checklist.md").write_text("1. Check VAT ID.\n")

    specs = _read_skills_folder(tmp_path)
    assert len(specs) == 1
    assert specs[0].reference_root == "skills/thai-compliance"


def test_root_skill_md_reference_root_is_the_capa_root_itself(tmp_path: Path) -> None:
    folder = tmp_path / "single_skill_plugin"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: Root Skill\n---\n\nDo the thing.")
    (folder / "references").mkdir()
    (folder / "references" / "notes.md").write_text("notes\n")

    table, _warnings = adapt_claude_plugin(folder, plugin_id="single_skill_plugin")
    assert table["skill_template"]["reference_root"] == ""


def test_discovered_plugin_manifest_carries_reference_root(tmp_path: Path) -> None:
    folder = tmp_path / "compliance_plugin"
    skills_dir = folder / "skills" / "thai-compliance"
    skills_dir.mkdir(parents=True)
    _write_plugin_toml(folder)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: Thai Compliance\n---\n\nSee references/checklist.md."
    )

    discovered = _read(folder)
    assert discovered.valid, discovered.error
    assert discovered.manifest is not None
    skill_pack = discovered.manifest["skill_pack"]
    assert skill_pack["skills"][0]["reference_root"] == "skills/thai-compliance"


@pytest.mark.asyncio
async def test_materialise_combines_capa_name_and_skill_subpath(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    manifest = Manifest(name="compliance_plugin", version="1.0.0")
    spec = SkillTemplateSpec(
        name="Thai Compliance",
        instruction="Follow the checklist.",
        reference_root="skills/thai-compliance",
    )
    async with app_session(tenant) as db:
        await _materialise_one_skill(db, tenant_id=tenant, manifest=manifest, spec=spec)
        await db.flush()
        version = (
            await db.execute(select(m.SkillVersion).where(m.SkillVersion.tenant_id == tenant))
        ).scalar_one()
        assert version.definition["reference_root"] == "compliance_plugin/skills/thai-compliance"


@pytest.mark.asyncio
async def test_materialise_root_skill_reference_root_is_bare_capa_name(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    manifest = Manifest(name="single_skill_plugin", version="1.0.0")
    spec = SkillTemplateSpec(
        name="Root Skill", instruction="Do the thing.", reference_root=""
    )
    async with app_session(tenant) as db:
        await _materialise_one_skill(db, tenant_id=tenant, manifest=manifest, spec=spec)
        await db.flush()
        version = (
            await db.execute(select(m.SkillVersion).where(m.SkillVersion.tenant_id == tenant))
        ).scalar_one()
        assert version.definition["reference_root"] == "single_skill_plugin"


@pytest.mark.asyncio
async def test_materialise_no_reference_root_stays_none(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    manifest = Manifest(name="toml_only_plugin", version="1.0.0")
    spec = SkillTemplateSpec(name="Greet", instruction="Say hello.")
    async with app_session(tenant) as db:
        await _materialise_one_skill(db, tenant_id=tenant, manifest=manifest, spec=spec)
        await db.flush()
        version = (
            await db.execute(select(m.SkillVersion).where(m.SkillVersion.tenant_id == tenant))
        ).scalar_one()
        assert version.definition["reference_root"] is None
