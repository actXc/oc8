"""Archive finished runs' evidence, chain it, and reduce it when its window closes.

§12.5.1 names two gaps in the audit trail, and this closes both: the session
material a runtime leaves behind is "outside the audit trail altogether --
neither chained, retained, nor pruned." Measured on the running system it is
also three orders of magnitude larger than the ledger, so it is where the cost
lives as well as where the hole is.

**The order of operations is the whole design.** For each finished run:

1. pack the evidence into one archive (`oc8.evidence.archive`) -- the source
   tree is left exactly where it is;
2. append `evidence.archived` to the tenant's hash chain, carrying the
   archive's SHA-256, and COMMIT;
3. only then delete the loose tree.

A crash between 2 and 3 leaves the evidence in both places, which the next tick
resolves. A crash the other way round -- deleting first -- would leave evidence
that vanished with nothing recording that it ever existed, which is precisely
the failure an audit trail exists to prevent. Reduction runs the same way: the
`evidence.reduced` entry is committed before the archive it describes is
removed, so the ledger can always answer "where did it go" even though it can no
longer answer "what did it say".

**What core knows about the evidence: nothing but where the runtime says it is.**
The layout, and which files inside it are the harness's own housekeeping rather
than a record of the agent's work, belong to the runtime plugin
(`EvidenceProducingRuntime`). Core owns the archive format, the hash, the ledger
entry and the clock.

**Why a column and not a filesystem scan.** `agent_run.evidence_state` is the
work list. Scanning every terminal run and stat()ing the disk would re-walk all
of history on every tick, for ever; the column narrows each tick to the runs
that still have something to do, and makes "already archived" a fact rather than
an inference from a file that happens to exist.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import inspect
import logging
import os
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, exists, select

from oc8 import models as m
from oc8.audit.chain import append_event
from oc8.config import get_settings
from oc8.db.session import tenant_session
from oc8.evidence.archive import ARCHIVE_SUFFIX, archive_dir
from oc8.runtime.states import TERMINAL, RunState
from oc8.triggers.scheduler import list_active_tenant_ids

logger = logging.getLogger(__name__)

#: How many runs one tenant may contribute to a single tick. The sweep runs on
#: the worker's housekeeping timer beside the run loop, so a backlog of ten
#: thousand runs must not turn one tick into an hour of compression. The backlog
#: drains over the following ticks; the column keeps the work list exact.
_BATCH = 200

ARCHIVED = "evidence.archived"
REDUCED = "evidence.reduced"
CATEGORY = "evidence"


@dataclass
class EvidenceSweepReport:
    """What one tick did. Returned rather than only logged so the CLI can print
    it and the tests can assert on it."""

    archived: list[uuid.UUID] = field(default_factory=list)
    reduced: list[uuid.UUID] = field(default_factory=list)
    raw_bytes: int = 0
    stored_bytes: int = 0
    dropped_bytes: int = 0
    reclaimed_bytes: int = 0
    invocations_pruned: int = 0
    failures: int = 0


def archive_root() -> str:
    """Where archives live. Empty setting means beside the session root."""
    settings = get_settings()
    return settings.evidence_archive_root or os.path.join(settings.runtime_session_root, "archive")


def archive_path(root: str, agent_id: uuid.UUID, run_id: uuid.UUID) -> str:
    """One file per run, under its agent. Core's own layout, not a runtime's."""
    return os.path.join(root, str(agent_id), f"{run_id}{ARCHIVE_SUFFIX}")


def _remove_tree(path: str) -> None:
    """Extracted so a test can make the delete fail and prove the ledger entry
    survived it."""
    shutil.rmtree(path)


def _evidence_source(runtime: Any) -> tuple[Callable[..., str | None], tuple[str, ...]] | None:
    """The optional evidence half of the runtime seam, or None.

    Duck-typed rather than isinstance'd against the Protocol: a runtime plugin
    is loaded from outside this package and only has to answer the two names,
    not import core's type to prove it.
    """
    getter = getattr(runtime, "evidence_dir", None)
    if not callable(getter):
        return None
    excludes = getattr(runtime, "evidence_excludes", ())
    return getter, tuple(excludes)


async def _resolve_runtime(db: Any, *, tenant_id: uuid.UUID, agent: m.Agent) -> Any:
    from oc8.runtime.registry import resolve_runtime

    return await resolve_runtime(db, tenant_id=tenant_id, agent=agent)


async def _call_resolver(resolver: Callable[..., Any], **kwargs: Any) -> Any:
    """Resolving a runtime is asynchronous for the real registry (it reads
    plugin rows) and synchronous for anything that already has one. Both are
    legitimate, so the seam takes either rather than forcing a caller to wrap a
    value it already holds in a coroutine."""
    result = resolver(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _archive_one(
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    agent_id: uuid.UUID,
    runtime: Any,
    now: dt.datetime,
    report: EvidenceSweepReport,
) -> None:
    """Archive one run's evidence, or record that it had none.

    Its own transaction, opened here rather than shared with the rest of the
    tick, because the tenant binding is transaction-LOCAL: `tenant_session` sets
    `app.tenant_id` with `is_local=true`, so the commit that makes this run's
    ledger entry durable also unbinds the session. Reusing it for the next run
    would query and write as an unbound tenant, which RLS answers with silence
    rather than an error -- a sweep that archived exactly one run per tick and
    reported success.
    """
    source = _evidence_source(runtime)
    src = None if source is None else source[0](agent_id=agent_id, run_id=run_id)
    if src is None:
        # Nothing on disk: either an in-process run, or a folder somebody has
        # already removed. Either way this run is done, and saying so is what
        # stops it being re-examined on every tick for the rest of time.
        async with tenant_session(tenant_id) as db:
            run = await db.get(m.AgentRun, run_id)
            if run is not None:
                run.evidence_state = "none"
                run.evidence_at = now
        return

    dest = archive_path(archive_root(), agent_id, run_id)
    excludes = source[1] if source else ()
    # Compression is CPU-bound and this runs inside the worker's event loop
    # beside live runs' heartbeats -- off-thread, or a big archive stalls them.
    result = await asyncio.to_thread(archive_dir, src, dest, excludes=excludes)

    async with tenant_session(tenant_id) as db:
        run = await db.get(m.AgentRun, run_id)
        if run is None or run.evidence_state != "present":
            return  # another worker archived it while this one was compressing
        await append_event(
            db,
            tenant_id=tenant_id,
            actor_type="system",
            actor_id=None,
            category=CATEGORY,
            action=ARCHIVED,
            resource={
                "run_id": str(run_id),
                "agent_id": str(agent_id),
                "format": "tar.xz",
                "sha256": result.sha256,
                "files": result.files,
                "raw_bytes": result.raw_bytes,
                "stored_bytes": result.stored_bytes,
                "dropped_files": result.dropped_files,
                "dropped_bytes": result.dropped_bytes,
                "skipped_files": result.skipped_files,
            },
            decision="allow",
        )
        run.evidence_state = "archived"
        run.evidence_at = now
    # THE commit just happened, on the way out of that block. Everything above
    # is recoverable; everything below destroys something. Nothing is deleted
    # until the ledger entry has landed.

    try:
        await asyncio.to_thread(_remove_tree, src)
    except OSError:
        # The archive holds the same bytes and the ledger points at it, so this
        # costs disk, not evidence. Left for the operator rather than retried
        # into a loop -- a tree that will not delete usually means the mount is
        # read-only or gone, which retrying does not fix.
        logger.warning(
            "evidence: archived run %s but could not remove %s", run_id, src, exc_info=True
        )
    else:
        report.reclaimed_bytes += max(result.raw_bytes + result.dropped_bytes, 0)

    report.archived.append(run_id)
    report.raw_bytes += result.raw_bytes
    report.stored_bytes += result.stored_bytes
    report.dropped_bytes += result.dropped_bytes


async def _reduce_one(
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    agent_id: uuid.UUID,
    now: dt.datetime,
    retention_days: int,
    report: EvidenceSweepReport,
) -> None:
    """Replace an archive with the ledger's memory of it.

    Its own transaction, for the reason spelled out in `_archive_one`.
    """
    dest = archive_path(archive_root(), agent_id, run_id)
    try:
        # One stat on a local path, taken before the ledger entry so the entry
        # can say how big the thing it is replacing was. Not the blocking IO
        # ASYNC240 is aimed at -- the compression and the unlink, which are,
        # both go through asyncio.to_thread.
        stored = os.path.getsize(dest)  # noqa: ASYNC240
    except OSError:
        stored = 0

    async with tenant_session(tenant_id) as db:
        run = await db.get(m.AgentRun, run_id)
        if run is None or run.evidence_state != "archived":
            return
        sha256 = await _archived_hash(db, tenant_id=tenant_id, run_id=run_id)
        await append_event(
            db,
            tenant_id=tenant_id,
            actor_type="system",
            actor_id=None,
            category=CATEGORY,
            action=REDUCED,
            resource={
                "run_id": str(run_id),
                "agent_id": str(agent_id),
                # Carried forward from the archive entry so this row alone
                # answers "what was there": the hash outlives the bytes it names.
                "sha256": sha256,
                "stored_bytes": stored,
                "retention_days": retention_days,
            },
            decision="allow",
            reason="evidence window closed",
        )
        run.evidence_state = "reduced"
        run.evidence_at = now

    try:
        if os.path.exists(dest):  # noqa: ASYNC240 -- one stat; see above
            await asyncio.to_thread(os.unlink, dest)
    except OSError:
        logger.warning("evidence: could not remove archive %s", dest, exc_info=True)
    else:
        report.reclaimed_bytes += stored
    report.reduced.append(run_id)


async def _archived_hash(db: Any, *, tenant_id: uuid.UUID, run_id: uuid.UUID) -> str | None:
    """The hash this run's archive was recorded under, read back from the chain.

    Read rather than recomputed: by the time an archive is reduced the point is
    to remember what its hash WAS, and recomputing it from a file that is about
    to be deleted would prove nothing about what the ledger already claims.
    """
    rows = (
        (
            await db.execute(
                select(m.AuditEvent.resource)
                .where(
                    m.AuditEvent.tenant_id == tenant_id,
                    m.AuditEvent.category == CATEGORY,
                    m.AuditEvent.action == ARCHIVED,
                    m.AuditEvent.resource["run_id"].astext == str(run_id),
                )
                .order_by(m.AuditEvent.seq.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    return (rows or {}).get("sha256")


async def _prune_invocations(
    tenant_id: uuid.UUID, *, cutoff: dt.datetime, report: EvidenceSweepReport
) -> None:
    """Drop idempotency-cache rows nothing can replay against any more.

    **Deleted rather than archived, because this is not evidence.** A call's
    ARGUMENTS are in the audit ledger for the full retention period, and its
    RESULT is in the run's transcript, which `_archive_one` hashes into the same
    chain. What `tool_invocation` adds on top is one operational ability: answer
    a REPEAT of the same call, within the same task, without acting twice. That
    ability is worthless once no run can execute that task again, and keeping a
    dead cache is not the same as keeping a record.

    **The predicate is about runs, not about `task.state`.** On this deployment
    65 tasks sit in `in_progress` behind a FAILED run, because the failure path
    never closed them; keying on that column would keep their rows for ever. A
    task is done with when at least one run of it exists and none of them is in
    a live state -- `waiting_for_approval` included, since a parked run resumes
    into the same task and reproduces the approved call, which is precisely when
    the cache has to be there.

    A row whose task has no run at all is left alone: unknown is not the same as
    done, the same rule the container reaper uses.
    """
    live = [s.value for s in RunState if s not in TERMINAL]
    async with tenant_session(tenant_id) as db:
        runs_of_task = (
            select(m.AgentRun.id)
            .where(
                m.AgentRun.tenant_id == tenant_id,
                m.AgentRun.task_id == m.ToolInvocation.task_id,
            )
            .correlate(m.ToolInvocation)
        )
        result = await db.execute(
            delete(m.ToolInvocation).where(
                m.ToolInvocation.tenant_id == tenant_id,
                m.ToolInvocation.created_at < cutoff,
                exists(runs_of_task),
                ~exists(runs_of_task.where(m.AgentRun.state.in_(live))),
            )
        )
        # CursorResult carries rowcount; the base Result type does not, and the
        # execute() overload for a DELETE is not narrow enough to say so.
        deleted = getattr(result, "rowcount", 0) or 0
    report.invocations_pruned += deleted


async def _sweep_tenant(
    tenant_id: uuid.UUID,
    *,
    now: dt.datetime,
    resolve: Callable[..., Any],
    report: EvidenceSweepReport,
) -> None:
    settings = get_settings()
    archive_cutoff = now - dt.timedelta(minutes=settings.evidence_archive_after_minutes)
    retention_days = settings.evidence_retention_days

    # The work list is read once, as plain ids, in a transaction that writes
    # nothing. Every unit of work below then opens its OWN transaction: the
    # tenant binding is transaction-local, so a session that commits mid-loop
    # goes tenant-blind for everything after it, and RLS answers a blind session
    # with silence rather than an error.
    async with tenant_session(tenant_id) as db:
        pending = list(
            (
                await db.execute(
                    select(m.AgentRun.id, m.AgentRun.agent_id)
                    .where(
                        m.AgentRun.evidence_state == "present",
                        m.AgentRun.state.in_([s.value for s in TERMINAL]),
                        m.AgentRun.updated_at < archive_cutoff,
                    )
                    .order_by(m.AgentRun.updated_at)
                    .limit(_BATCH)
                )
            ).all()
        )
        reduce_cutoff = now - dt.timedelta(days=max(retention_days, 0))
        expired = (
            []
            if retention_days <= 0
            else list(
                (
                    await db.execute(
                        select(m.AgentRun.id, m.AgentRun.agent_id)
                        .where(
                            m.AgentRun.evidence_state == "archived",
                            m.AgentRun.evidence_at < reduce_cutoff,
                        )
                        .order_by(m.AgentRun.evidence_at)
                        .limit(_BATCH)
                    )
                ).all()
            )
        )

    runtimes: dict[uuid.UUID, Any] = {}
    for run_id, agent_id in pending:
        try:
            if agent_id not in runtimes:
                async with tenant_session(tenant_id) as db:
                    agent = await db.get(m.Agent, agent_id)
                    runtimes[agent_id] = (
                        None
                        if agent is None
                        else await _call_resolver(resolve, db=db, tenant_id=tenant_id, agent=agent)
                    )
            runtime = runtimes[agent_id]
            if runtime is None:
                # No agent row, so no runtime to ask. Not an error worth a
                # failure count -- but not retried for ever either.
                async with tenant_session(tenant_id) as db:
                    run = await db.get(m.AgentRun, run_id)
                    if run is not None and run.evidence_state == "present":
                        run.evidence_state = "none"
                        run.evidence_at = now
                continue
            await _archive_one(
                tenant_id=tenant_id,
                run_id=run_id,
                agent_id=agent_id,
                runtime=runtime,
                now=now,
                report=report,
            )
        except Exception:
            # One run's evidence must not cost every other run's. The row keeps
            # `present`, so the next tick tries again.
            report.failures += 1
            logger.warning("evidence: could not archive run %s", run_id, exc_info=True)

    # Same grace window as the archive half, and for the same reason: nothing
    # that just stopped should be swept while somebody may still be looking at
    # it. Its own transaction, like every other unit of work here.
    try:
        await _prune_invocations(tenant_id, cutoff=archive_cutoff, report=report)
    except Exception:
        report.failures += 1
        logger.warning("evidence: could not prune tool invocations", exc_info=True)

    for run_id, agent_id in expired:
        try:
            await _reduce_one(
                tenant_id=tenant_id,
                run_id=run_id,
                agent_id=agent_id,
                now=now,
                retention_days=retention_days,
                report=report,
            )
        except Exception:
            report.failures += 1
            logger.warning("evidence: could not reduce run %s", run_id, exc_info=True)


async def sweep_evidence(
    *,
    now: dt.datetime | None = None,
    resolve: Callable[..., Any] | None = None,
) -> EvidenceSweepReport:
    """One tick. Never raises: this runs on the worker's housekeeping timer and
    a failure here must not stop a worker.

    `resolve` is injectable for the same reason the container reaper takes a
    docker client: the sweep's logic is worth testing without a plugin registry
    behind it.
    """
    report = EvidenceSweepReport()
    if not get_settings().evidence_sweep_enabled:
        return report
    now = now or dt.datetime.now(tz=dt.UTC)
    resolver = resolve or _resolve_runtime

    try:
        tenants = await list_active_tenant_ids()
    except Exception:
        logger.warning("evidence: could not list tenants; skipping tick", exc_info=True)
        return report

    for tenant_id in tenants:
        try:
            await _sweep_tenant(tenant_id, now=now, resolve=resolver, report=report)
        except Exception:
            report.failures += 1
            logger.warning("evidence: sweep failed for tenant %s", tenant_id, exc_info=True)

    if report.archived or report.reduced or report.invocations_pruned:
        logger.info(
            "evidence: archived %d run(s) (%.1f kB -> %.1f kB, %.1f kB of runtime "
            "housekeeping dropped), reduced %d, pruned %d spent idempotency row(s)",
            len(report.archived),
            report.raw_bytes / 1024,
            report.stored_bytes / 1024,
            report.dropped_bytes / 1024,
            len(report.reduced),
            report.invocations_pruned,
        )
    return report
