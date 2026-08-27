"""The sweep that archives finished runs' evidence and reduces it on time.

Two things make this dangerous rather than routine: it deletes files, and the
files it deletes are the only record of why an agent did what it did. So most of
what is tested here is what must be LEFT ALONE, and the rest is ordering -- the
ledger entry has to be committed before the bytes it points at can go.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update

from oc8 import models as m
from oc8.config import get_settings
from oc8.evidence.sweep import archive_path, sweep_evidence
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

LONG_AGO = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)


class FakeRuntime:
    """Stands in for a container runtime that leaves a session folder behind."""

    evidence_excludes = ("junk",)

    def __init__(self, root: str) -> None:
        self._root = root

    def evidence_dir(self, *, agent_id: uuid.UUID, run_id: uuid.UUID) -> str | None:
        path = os.path.join(self._root, str(agent_id), str(run_id))
        return path if os.path.isdir(path) else None


class NoEvidenceRuntime:
    """The in-process runtime: it writes nothing to disk."""


def _write_evidence(root: str, agent_id: uuid.UUID, run_id: uuid.UUID) -> str:
    path = os.path.join(root, str(agent_id), str(run_id))
    os.makedirs(os.path.join(path, "agent"), exist_ok=True)
    os.makedirs(os.path.join(path, "junk"), exist_ok=True)
    with open(os.path.join(path, "agent", "CLAUDE.md"), "w") as fh:
        fh.write("standing instructions\n" * 50)
    with open(os.path.join(path, "junk", "telemetry.json"), "w") as fh:
        fh.write("x" * 4000)
    return path


async def _run(db: Any, tenant: uuid.UUID, *, state: str = "done") -> m.AgentRun:
    if await db.get(m.Organization, tenant) is None:
        db.add(m.Organization(id=tenant, slug=f"t-{tenant.hex[:8]}", name="T"))
        await db.flush()
    dept = m.Department(tenant_id=tenant, name="Kundenservice", frame={})
    db.add(dept)
    await db.flush()
    agent = m.Agent(
        tenant_id=tenant,
        department_id=dept.id,
        name="Sina",
        status="idle",
        narrowing={},
        definition={},
        presentation={},
    )
    db.add(agent)
    await db.flush()
    run = m.AgentRun(tenant_id=tenant, agent_id=agent.id, state=state, context={})
    db.add(run)
    await db.flush()
    return run


async def _age(db: Any, *run_ids: uuid.UUID, when: dt.datetime) -> None:
    """Backdate rows past the grace window.

    All of them in ONE statement before ONE commit, because `tenant_session`
    binds the tenant with a transaction-LOCAL GUC: the first commit unbinds the
    session, and a second UPDATE would then match zero rows under RLS and say
    nothing about it.
    """
    await db.execute(update(m.AgentRun).where(m.AgentRun.id.in_(run_ids)).values(updated_at=when))
    await db.commit()


@pytest.fixture
def evidence_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """Enable the sweep against a throwaway session root and archive root."""
    settings = get_settings()
    sessions = str(tmp_path / "sessions")
    archives = str(tmp_path / "archive")
    os.makedirs(sessions, exist_ok=True)
    monkeypatch.setattr(settings, "evidence_sweep_enabled", True, raising=False)
    monkeypatch.setattr(settings, "evidence_archive_root", archives, raising=False)
    monkeypatch.setattr(settings, "evidence_archive_after_minutes", 60, raising=False)
    monkeypatch.setattr(settings, "evidence_retention_days", 0, raising=False)
    return sessions, archives


async def _events(db: Any, tenant: uuid.UUID, run_id: uuid.UUID) -> list[m.AuditEvent]:
    rows = (
        (
            await db.execute(
                select(m.AuditEvent)
                .where(m.AuditEvent.tenant_id == tenant, m.AuditEvent.category == "evidence")
                .order_by(m.AuditEvent.seq)
            )
        )
        .scalars()
        .all()
    )
    return [e for e in rows if e.resource.get("run_id") == str(run_id)]


async def test_a_finished_run_is_archived_chained_and_pruned(
    app_session: AppSessionFactory, evidence_on: tuple[str, str]
) -> None:
    sessions, archives = evidence_on
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        run_id, agent_id = run.id, run.agent_id
        tree = _write_evidence(sessions, agent_id, run_id)
        await _age(db, run_id, when=LONG_AGO)

    report = await sweep_evidence(resolve=lambda **_: FakeRuntime(sessions))

    assert run_id in report.archived
    dest = archive_path(archives, agent_id, run_id)
    assert os.path.exists(dest), "the archive is what the ledger points at"
    assert not os.path.exists(tree), "the loose tree is gone once it is safely inside"

    async with app_session(tenant) as db:
        events = await _events(db, tenant, run_id)
        assert [e.action for e in events] == ["evidence.archived"]
        resource = events[0].resource
        assert resource["sha256"] == _sha256(dest)
        assert resource["files"] == 1, "only CLAUDE.md; the junk was dropped"
        assert resource["dropped_bytes"] == 4000
        after = await db.get(m.AgentRun, run_id)
        assert after is not None and after.evidence_state == "archived"


def _sha256(path: str) -> str:
    import hashlib

    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


async def test_a_running_run_is_never_touched(
    app_session: AppSessionFactory, evidence_on: tuple[str, str]
) -> None:
    """Its container is still writing into that folder."""
    sessions, _ = evidence_on
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant, state="running")
        run_id, agent_id = run.id, run.agent_id
        tree = _write_evidence(sessions, agent_id, run_id)
        await _age(db, run_id, when=LONG_AGO)

    report = await sweep_evidence(resolve=lambda **_: FakeRuntime(sessions))

    assert run_id not in report.archived
    assert os.path.exists(tree)


async def test_a_run_waiting_for_approval_is_never_touched(
    app_session: AppSessionFactory, evidence_on: tuple[str, str]
) -> None:
    """A parked run resumes into the SAME session folder -- taking it would
    make the resumed leg unable to find the conversation it is continuing."""
    sessions, _ = evidence_on
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant, state="waiting_for_approval")
        run_id, agent_id = run.id, run.agent_id
        tree = _write_evidence(sessions, agent_id, run_id)
        await _age(db, run_id, when=LONG_AGO)

    await sweep_evidence(resolve=lambda **_: FakeRuntime(sessions))

    assert os.path.exists(tree)


async def test_a_just_finished_run_is_left_alone(
    app_session: AppSessionFactory, evidence_on: tuple[str, str]
) -> None:
    """The grace window: an operator reading a run that failed a minute ago
    must not race the sweep."""
    sessions, _ = evidence_on
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant, state="failed")
        run_id, agent_id = run.id, run.agent_id
        tree = _write_evidence(sessions, agent_id, run_id)
        await db.commit()

    report = await sweep_evidence(resolve=lambda **_: FakeRuntime(sessions))

    assert run_id not in report.archived
    assert os.path.exists(tree)


async def test_a_disabled_sweep_does_nothing(
    app_session: AppSessionFactory,
    evidence_on: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, _ = evidence_on
    monkeypatch.setattr(get_settings(), "evidence_sweep_enabled", False, raising=False)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        run_id, agent_id = run.id, run.agent_id
        tree = _write_evidence(sessions, agent_id, run_id)
        await _age(db, run_id, when=LONG_AGO)

    report = await sweep_evidence(resolve=lambda **_: FakeRuntime(sessions))

    assert report.archived == []
    assert os.path.exists(tree)


async def test_a_run_that_wrote_no_evidence_is_marked_none_not_retried(
    app_session: AppSessionFactory, evidence_on: tuple[str, str]
) -> None:
    """Otherwise every run that never had a folder is stat()ed for ever."""
    sessions, _ = evidence_on
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        run_id = run.id
        await _age(db, run_id, when=LONG_AGO)

    report = await sweep_evidence(resolve=lambda **_: FakeRuntime(sessions))

    assert run_id not in report.archived
    async with app_session(tenant) as db:
        after = await db.get(m.AgentRun, run_id)
        assert after is not None and after.evidence_state == "none"
        assert await _events(db, tenant, run_id) == []


async def test_a_runtime_with_no_evidence_at_all_is_marked_none(
    app_session: AppSessionFactory, evidence_on: tuple[str, str]
) -> None:
    """The in-process runtime writes nothing to disk, so its runs have no
    evidence directory to ask about."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        run_id = run.id
        await _age(db, run_id, when=LONG_AGO)

    await sweep_evidence(resolve=lambda **_: NoEvidenceRuntime())

    async with app_session(tenant) as db:
        after = await db.get(m.AgentRun, run_id)
        assert after is not None and after.evidence_state == "none"


async def test_retention_reduces_an_archive_to_its_ledger_entry(
    app_session: AppSessionFactory,
    evidence_on: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§12.5.1: retention is reduction. The proof of WHAT happened survives;
    only the ability to re-read the reasoning expires -- and the ledger says so
    explicitly, so nobody has to guess when it went."""
    sessions, archives = evidence_on
    monkeypatch.setattr(get_settings(), "evidence_retention_days", 30, raising=False)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        run_id, agent_id = run.id, run.agent_id
        _write_evidence(sessions, agent_id, run_id)
        await _age(db, run_id, when=LONG_AGO)

    await sweep_evidence(resolve=lambda **_: FakeRuntime(sessions))
    dest = archive_path(archives, agent_id, run_id)
    archived_sha = _sha256(dest)
    # Backdate the archive past the window.
    async with app_session(tenant) as db:
        await db.execute(
            update(m.AgentRun).where(m.AgentRun.id == run_id).values(evidence_at=LONG_AGO)
        )
        await db.commit()

    report = await sweep_evidence(resolve=lambda **_: FakeRuntime(sessions))

    assert run_id in report.reduced
    assert not os.path.exists(dest)
    async with app_session(tenant) as db:
        events = await _events(db, tenant, run_id)
        assert [e.action for e in events] == ["evidence.archived", "evidence.reduced"]
        assert events[1].resource["sha256"] == archived_sha, (
            "the hash outlives the bytes: it is what the ledger keeps pointing at"
        )
        after = await db.get(m.AgentRun, run_id)
        assert after is not None and after.evidence_state == "reduced"


async def test_retention_zero_keeps_everything(
    app_session: AppSessionFactory, evidence_on: tuple[str, str]
) -> None:
    """The default. Archiving is lossless; only a window an operator chooses
    makes it lossy."""
    sessions, archives = evidence_on
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        run_id, agent_id = run.id, run.agent_id
        _write_evidence(sessions, agent_id, run_id)
        await _age(db, run_id, when=LONG_AGO)

    await sweep_evidence(resolve=lambda **_: FakeRuntime(sessions))
    async with app_session(tenant) as db:
        await db.execute(
            update(m.AgentRun).where(m.AgentRun.id == run_id).values(evidence_at=LONG_AGO)
        )
        await db.commit()

    report = await sweep_evidence(resolve=lambda **_: FakeRuntime(sessions))

    assert report.reduced == []
    assert os.path.exists(archive_path(archives, agent_id, run_id))


async def test_a_second_sweep_does_not_archive_twice(
    app_session: AppSessionFactory, evidence_on: tuple[str, str]
) -> None:
    sessions, _ = evidence_on
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        run_id, agent_id = run.id, run.agent_id
        _write_evidence(sessions, agent_id, run_id)
        await _age(db, run_id, when=LONG_AGO)

    await sweep_evidence(resolve=lambda **_: FakeRuntime(sessions))
    second = await sweep_evidence(resolve=lambda **_: FakeRuntime(sessions))

    assert run_id not in second.archived
    async with app_session(tenant) as db:
        assert len(await _events(db, tenant, run_id)) == 1


async def test_the_ledger_entry_is_committed_before_the_tree_is_deleted(
    app_session: AppSessionFactory,
    evidence_on: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash between the two must leave the evidence in BOTH places, never in
    neither. Proven by making the delete fail: the archive and its ledger row
    survive, and the run is not left claiming its evidence is still on disk."""
    sessions, archives = evidence_on
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        run_id, agent_id = run.id, run.agent_id
        _write_evidence(sessions, agent_id, run_id)
        await _age(db, run_id, when=LONG_AGO)

    import oc8.evidence.sweep as sweep_mod

    def refuse(_path: str) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(sweep_mod, "_remove_tree", refuse)
    report = await sweep_evidence(resolve=lambda **_: FakeRuntime(sessions))

    assert run_id in report.archived
    assert os.path.exists(archive_path(archives, agent_id, run_id))
    async with app_session(tenant) as db:
        assert [e.action for e in await _events(db, tenant, run_id)] == ["evidence.archived"]
        after = await db.get(m.AgentRun, run_id)
        assert after is not None and after.evidence_state == "archived"


async def test_one_bad_run_does_not_stop_the_others(
    app_session: AppSessionFactory, evidence_on: tuple[str, str]
) -> None:
    sessions, _ = evidence_on
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        bad = await _run(db, tenant)
        good = await _run(db, tenant)
        bad_id, good_id = bad.id, good.id
        _write_evidence(sessions, bad.agent_id, bad_id)
        _write_evidence(sessions, good.agent_id, good_id)
        await _age(db, bad_id, good_id, when=LONG_AGO)

    class Halfbroken(FakeRuntime):
        def evidence_dir(self, *, agent_id: uuid.UUID, run_id: uuid.UUID) -> str | None:
            if run_id == bad_id:
                raise RuntimeError("the plugin is unhappy")
            return super().evidence_dir(agent_id=agent_id, run_id=run_id)

    report = await sweep_evidence(resolve=lambda **_: Halfbroken(sessions))

    assert good_id in report.archived
    assert report.failures == 1


async def test_several_runs_are_archived_in_one_tick(
    app_session: AppSessionFactory, evidence_on: tuple[str, str]
) -> None:
    """The one that nearly shipped broken.

    `tenant_session` binds the tenant with a transaction-LOCAL GUC, so the
    commit that makes the FIRST run's ledger entry durable also unbinds the
    session. Every query after it then runs unbound, and RLS answers an unbound
    session with an empty result rather than an error -- so the sweep would
    archive exactly one run per tick, for ever, and report success.
    """
    sessions, archives = evidence_on
    tenant = uuid.uuid4()
    ids: list[tuple[uuid.UUID, uuid.UUID]] = []
    async with app_session(tenant) as db:
        for _ in range(3):
            run = await _run(db, tenant)
            ids.append((run.id, run.agent_id))
            _write_evidence(sessions, run.agent_id, run.id)
        await _age(db, *[r for r, _ in ids], when=LONG_AGO)

    report = await sweep_evidence(resolve=lambda **_: FakeRuntime(sessions))

    assert {r for r, _ in ids} <= set(report.archived)
    for run_id, agent_id in ids:
        assert os.path.exists(archive_path(archives, agent_id, run_id))
    async with app_session(tenant) as db:
        for run_id, _ in ids:
            assert len(await _events(db, tenant, run_id)) == 1


async def test_archives_of_one_agent_sit_together_under_it(
    app_session: AppSessionFactory, evidence_on: tuple[str, str]
) -> None:
    """Layout is core's, not a runtime's: one file per run, under its agent."""
    _, archives = evidence_on
    agent_id, run_id = uuid.uuid4(), uuid.uuid4()

    assert archive_path(archives, agent_id, run_id) == os.path.join(
        archives, str(agent_id), f"{run_id}.tar.xz"
    )


async def test_it_reports_what_it_saved(
    app_session: AppSessionFactory, evidence_on: tuple[str, str]
) -> None:
    """The numbers an operator needs to see whether this is worth running."""
    sessions, _ = evidence_on
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = await _run(db, tenant)
        run_id, agent_id = run.id, run.agent_id
        _write_evidence(sessions, agent_id, run_id)
        await _age(db, run_id, when=LONG_AGO)

    report = await sweep_evidence(resolve=lambda **_: FakeRuntime(sessions))

    assert report.raw_bytes == len("standing instructions\n" * 50)
    assert report.dropped_bytes == 4000
    assert 0 < report.stored_bytes < report.raw_bytes
