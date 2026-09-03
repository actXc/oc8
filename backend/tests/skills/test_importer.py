"""Bringing a skill in from somewhere else, with a verdict attached.

The envelope of a Claude Code skill maps onto oc8's almost one-to-one, which
makes a blind importer tempting. Two things stop it being right: a body of
104,000 characters (measured, on a real machine) becomes a tool result that
sits in the transcript for the whole run, and the text is an INSTRUCTION an
agent will follow — imported from a stranger.

So most of these tests are about refusing, flagging and previewing.
"""

from __future__ import annotations

import io
import tarfile

import pytest

from oc8.knowledge.connectors.base import ConnectorError
from oc8.skills.importer import archive_url, parse_skill, read_archive
from tests.conftest import AppSessionFactory

SMALL = """---
name: Sauber übergeben
description: Eine Sache so weitergeben, dass der Nächste sie aufnehmen kann.
---

Nenne die Kennung, was getan ist, und was als Nächstes ansteht.
"""


def test_a_short_skill_fits() -> None:
    c = parse_skill(SMALL, path="a/SKILL.md", budget_tokens=1000)
    assert c is not None
    assert c.name == "Sauber übergeben"
    assert c.description.startswith("Eine Sache")
    assert c.verdict == "fits"
    assert c.warnings == []


def test_a_huge_skill_is_refused_against_the_budget() -> None:
    """104,000 characters is roughly 26,000 tokens. The runs that failed here
    were 189 tokens over the window."""
    body = "Sehr ausführliche Anweisung. " * 4000
    c = parse_skill(f"---\nname: review\n---\n\n{body}", path="r/SKILL.md", budget_tokens=1000)
    assert c is not None
    assert c.tokens > 20_000
    assert c.verdict == "too_big"


def test_the_same_skill_fits_behind_a_bigger_model() -> None:
    """It is not the skill that is too long; it is the window that is small.
    The verdict belongs to the DEPARTMENT, never to a fixed number."""
    body = "Anweisung. " * 400
    text = f"---\nname: mittel\n---\n\n{body}"
    assert parse_skill(text, path="m/SKILL.md", budget_tokens=200).verdict == "too_big"
    assert parse_skill(text, path="m/SKILL.md", budget_tokens=100_000).verdict == "fits"


def test_tools_an_oc8_agent_does_not_have_are_flagged() -> None:
    """An oc8 agent sits on a network with no route off the host and acts only
    through its connections. A skill built around a shell cannot work here."""
    text = "---\nname: ship\nallowed-tools:\n  - Bash\n  - Read\n---\n\nMach etwas."
    c = parse_skill(text, path="s/SKILL.md", budget_tokens=1000)
    assert c is not None
    assert any("bash" in w for w in c.warnings)


def test_bundled_files_are_flagged() -> None:
    # A direct import (this module's own flow) never gets a reference_root --
    # only capas/materialise.py sets one, for a skill installed via an actual
    # capa on disk (see capas/manifest.py's SkillTemplateSpec.reference_root).
    # So read_reference_file can never serve one imported this way, whatever
    # oc8 supports for a properly-installed capa's own skills.
    text = "---\nname: x\n---\n\nLies references/details.md und dann scripts/run.sh."
    c = parse_skill(text, path="x/SKILL.md", budget_tokens=1000)
    assert c is not None
    assert any("read_reference_file" in w for w in c.warnings)


def _tar_with_paths(entries: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in entries.items():
            data = content if isinstance(content, bytes) else content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_bundled_files_under_a_nested_skill_are_captured_relative_to_its_own_dir() -> None:
    blob = _tar_with_paths(
        {
            "repo-abc/skills/one/SKILL.md": SMALL,
            "repo-abc/skills/one/references/checklist.md": "1. Check VAT ID.\n",
            "repo-abc/skills/one/scripts/run.sh": "echo hi\n",
            # A file belonging to a DIFFERENT skill must never leak in here.
            "repo-abc/skills/two/references/other.md": "not this skill's file\n",
        }
    )
    found = read_archive(blob, budget_tokens=1000)
    assert len(found) == 1
    assert found[0].bundled_files == {
        "references/checklist.md": b"1. Check VAT ID.\n",
        "scripts/run.sh": b"echo hi\n",
    }


def test_bundled_files_at_the_archive_root_are_captured_too() -> None:
    blob = _tar_with_paths(
        {
            "repo-abc/SKILL.md": SMALL,
            "repo-abc/references/checklist.md": "root-level reference\n",
        }
    )
    found = read_archive(blob, budget_tokens=1000)
    assert len(found) == 1
    assert found[0].bundled_files == {"references/checklist.md": b"root-level reference\n"}


def test_a_skill_with_no_bundled_files_gets_an_empty_dict() -> None:
    blob = _tar_with_paths({"repo-abc/skills/one/SKILL.md": SMALL})
    found = read_archive(blob, budget_tokens=1000)
    assert found[0].bundled_files == {}


def test_bundled_files_are_not_serialised_into_the_preview_json() -> None:
    blob = _tar_with_paths(
        {
            "repo-abc/skills/one/SKILL.md": SMALL,
            "repo-abc/skills/one/references/checklist.md": "x\n",
        }
    )
    found = read_archive(blob, budget_tokens=1000)
    assert "bundled_files" not in found[0].to_json()


def test_an_oversized_bundled_file_is_skipped_not_refused() -> None:
    blob = _tar_with_paths(
        {
            "repo-abc/skills/one/SKILL.md": SMALL,
            "repo-abc/skills/one/references/huge.md": "x" * 250_000,
            "repo-abc/skills/one/references/small.md": "kept\n",
        }
    )
    found = read_archive(blob, budget_tokens=1000)
    assert found[0].verdict == "fits"  # the skill itself still imports fine
    assert "references/huge.md" not in found[0].bundled_files
    assert found[0].bundled_files["references/small.md"] == b"kept\n"


def test_the_total_bundle_size_is_capped_per_skill() -> None:
    # Eleven files at exactly the 200_000-byte PER-FILE cap (_MAX_BUNDLED_FILE_BYTES)
    # -- none individually oversized, so the per-file check never rejects one.
    # Eleven of them total 2_200_000, over the 2_000_000 total cap
    # (_MAX_BUNDLED_TOTAL_BYTES). Collection stops once the running total would
    # exceed that cap; files are read in the archive's own member order, so
    # exactly ten are kept here (10 * 200_000 = 2_000_000, the eleventh would
    # push it to 2_200_000).
    entries: dict[str, str | bytes] = {"repo-abc/skills/one/SKILL.md": SMALL}
    for i in range(11):
        entries[f"repo-abc/skills/one/references/f{i}.md"] = "x" * 200_000
    blob = _tar_with_paths(entries)
    found = read_archive(blob, budget_tokens=1000)
    total = sum(len(v) for v in found[0].bundled_files.values())
    assert total <= 2_000_000
    assert len(found[0].bundled_files) == 10


def test_something_that_is_not_a_skill_is_not_one() -> None:
    assert parse_skill("# Just a readme\n", path="README.md", budget_tokens=1000) is None
    assert parse_skill("---\ndescription: no name\n---\n\nbody", path="a", budget_tokens=1) is None
    assert parse_skill("---\nname: leer\n---\n\n", path="a", budget_tokens=1) is None


def test_a_git_url_becomes_an_archive_url() -> None:
    assert archive_url("https://github.com/thedotmack/claude-mem.git").startswith(
        "https://codeload.github.com/thedotmack/claude-mem/tar.gz"
    )
    assert archive_url("https://example.com/x.tar.gz") == "https://example.com/x.tar.gz"


def test_a_source_nobody_can_read_is_refused_with_a_usable_message() -> None:
    with pytest.raises(ConnectorError) as exc:
        archive_url("git@github.com:owner/repo.git")
    assert "https://github.com/owner/repo" in str(exc.value)


def _tar(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in entries.items():
            data = text.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_an_archive_yields_every_skill_and_ignores_the_rest() -> None:
    blob = _tar(
        {
            "repo-abc/skills/one/SKILL.md": SMALL,
            "repo-abc/skills/two/SKILL.md": SMALL.replace("Sauber übergeben", "Zweiter"),
            "repo-abc/README.md": "# nope",
            "repo-abc/scripts/run.sh": "echo hi",
        }
    )
    found = read_archive(blob, budget_tokens=1000)
    assert [c.name for c in found] == ["Sauber übergeben", "Zweiter"]
    # The archive's top directory carries a commit hash and must not leak into
    # what an operator is shown.
    assert all(not c.path.startswith("repo-abc") for c in found)


# --------------------------------------------------- the write path, end to end

@pytest.mark.asyncio
async def test_an_imported_skill_is_a_real_row_marked_as_foreign(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parser tests never touched the database, so the first real import
    died on a check constraint the code had never met. Anything that WRITES has
    to be tested against the schema, not against the shape of a dataclass."""
    import uuid as _uuid

    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select

    from oc8 import models as m
    from oc8.auth import get_identity_provider
    from oc8.main import create_app

    blob = _tar({"repo-abc/skills/one/SKILL.md": SMALL})

    async def _fake_fetch(url: str, **kw: object) -> bytes:
        return blob

    monkeypatch.setattr("oc8.skills.importer.safe_fetch_bytes", _fake_fetch)

    tenant = _uuid.uuid4()
    token = get_identity_provider().mint(
        tenant_id=tenant, subject="op", role="org_admin"
    )
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/skills/import",
                headers={"Authorization": f"Bearer {token}"},
                json={"source": "https://github.com/o/r", "names": ["Sauber übergeben"]},
            )
    assert r.status_code == 201, r.text
    assert [s["name"] for s in r.json()["imported"]] == ["Sauber übergeben"]

    async with app_session(tenant) as db:
        skill = (
            await db.execute(select(m.Skill).where(m.Skill.name == "Sauber übergeben"))
        ).scalars().first()
        assert skill is not None
        assert skill.trust_level == "community", "an import is never first_party"
        assert skill.author == "https://github.com/o/r", "the source travels with it"
        assert skill.current_version_id is not None


@pytest.mark.asyncio
async def test_an_imported_skill_with_bundled_files_gets_a_reference_root(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: import a skill whose body tells the agent to read a
    references/ file, and confirm the file actually lands somewhere
    read_reference_file (agent/control_tools.py) can serve it from."""
    import uuid as _uuid

    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select

    from oc8 import models as m
    from oc8.auth import get_identity_provider
    from oc8.main import create_app

    blob = _tar_with_paths(
        {
            "repo-abc/skills/one/SKILL.md": SMALL,
            "repo-abc/skills/one/references/checklist.md": "1. Check VAT ID.\n",
        }
    )

    async def _fake_fetch(url: str, **kw: object) -> bytes:
        return blob

    monkeypatch.setattr("oc8.skills.importer.safe_fetch_bytes", _fake_fetch)

    tenant = _uuid.uuid4()
    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role="org_admin")
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/skills/import",
                headers={"Authorization": f"Bearer {token}"},
                json={"source": "https://github.com/o/r", "names": ["Sauber übergeben"]},
            )
    assert r.status_code == 201, r.text

    async with app_session(tenant) as db:
        skill = (
            await db.execute(select(m.Skill).where(m.Skill.name == "Sauber übergeben"))
        ).scalar_one()
        version = await db.get(m.SkillVersion, skill.current_version_id)
        assert version is not None
        reference_root = version.definition.get("reference_root")
        assert reference_root == f"imported:{version.id}"

        stored = (
            await db.execute(
                select(m.ImportedSkillFile).where(
                    m.ImportedSkillFile.skill_version_id == version.id
                )
            )
        ).scalars().all()
        assert len(stored) == 1
        assert stored[0].rel_path == "references/checklist.md"
        assert stored[0].content == b"1. Check VAT ID.\n"


@pytest.mark.asyncio
async def test_an_imported_skill_with_no_bundled_files_gets_no_reference_root(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unremarkable case, worth asserting explicitly: nothing about a plain
    import (no bundled files) should start writing reference_root or rows."""
    import uuid as _uuid

    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select

    from oc8 import models as m
    from oc8.auth import get_identity_provider
    from oc8.main import create_app

    blob = _tar_with_paths({"repo-abc/skills/one/SKILL.md": SMALL})

    async def _fake_fetch(url: str, **kw: object) -> bytes:
        return blob

    monkeypatch.setattr("oc8.skills.importer.safe_fetch_bytes", _fake_fetch)

    tenant = _uuid.uuid4()
    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role="org_admin")
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/skills/import",
                headers={"Authorization": f"Bearer {token}"},
                json={"source": "https://github.com/o/r", "names": ["Sauber übergeben"]},
            )
    assert r.status_code == 201, r.text

    async with app_session(tenant) as db:
        skill = (
            await db.execute(select(m.Skill).where(m.Skill.name == "Sauber übergeben"))
        ).scalar_one()
        version = await db.get(m.SkillVersion, skill.current_version_id)
        assert version is not None
        assert version.definition.get("reference_root") is None


@pytest.mark.asyncio
async def test_an_explicit_budget_overrides_the_derived_one(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A derived number cannot cover every case: a model whose window nobody
    recorded, or one skill an operator judges worth the room. The override is
    per request, so it never quietly becomes the new normal."""
    import uuid as _uuid

    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from oc8.auth import get_identity_provider
    from oc8.main import create_app

    body = "Anweisung. " * 400  # ~1100 tokens
    blob = _tar({"r/skills/big/SKILL.md": f"---\nname: big\n---\n\n{body}"})

    async def _fake_fetch(url: str, **kw: object) -> bytes:
        return blob

    monkeypatch.setattr("oc8.skills.importer.safe_fetch_bytes", _fake_fetch)
    token = get_identity_provider().mint(
        tenant_id=_uuid.uuid4(), subject="op", role="org_admin"
    )
    app = create_app()

    async def _preview(budget: int | None) -> dict[str, object]:
        async with LifespanManager(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                r = await c.post(
                    "/api/v1/skills/import/preview",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"source": "https://github.com/o/r", "budgetTokens": budget},
                )
        assert r.status_code == 200, r.text
        return r.json()

    # Nothing recorded anywhere: no budget, and therefore no verdict against it.
    # A default here would be a number oc8 made up.
    none_set = await _preview(None)
    assert none_set["budgetTokens"] == 0
    assert none_set["skills"][0]["verdict"] == "fits"

    strict = await _preview(500)
    assert strict["budgetTokens"] == 500
    assert strict["skills"][0]["verdict"] == "too_big"

    generous = await _preview(20_000)
    assert generous["budgetTokens"] == 20_000
    assert generous["skills"][0]["verdict"] == "fits"


def test_without_a_budget_nothing_is_judged_too_big() -> None:
    """Where nobody has recorded what the model can hold, oc8 does not know.
    Sizes are reported so an operator can judge; a made-up ceiling would be a
    judgement dressed as a fact — and would quietly become the rule."""
    body = "Sehr lange Anweisung. " * 5000
    c = parse_skill(f"---\nname: riesig\n---\n\n{body}", path="r/SKILL.md", budget_tokens=0)
    assert c is not None
    assert c.tokens > 25_000
    assert c.verdict == "fits"
    # The size is still there to look at.
    assert c.to_json()["tokens"] == c.tokens
