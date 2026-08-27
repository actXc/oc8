"""Close runs that nothing is running any more.

There are two ways a run ends up abandoned, and neither is covered by the
machinery that already exists:

* The worker is killed mid-run. Its stream entry stays unacked, and after the
  idle window another worker reclaims it and fails the run. That path works.
* The worker ACKED the entry and then the work died anyway -- the container was
  killed, the host restarted, the process wedged. There is no entry left to
  reclaim, so nobody ever looks at that run again.

The second case leaves a row in `running` for ever. Observed live, 2026-07-28:
nine runs marked running with two containers alive, three of the rows five
hours old. The cost is not the row. It is that the agent looks permanently busy
on its own board, its containers are invisible to the container reaper (which
only removes containers of FINISHED runs), and the run's task never reaches
anyone -- a failure nobody is told about is worse than a failure.

This is the inverse sweep to sandbox.reaper: that one removes containers whose
run has finished, this one finishes runs that stopped reporting in. It decides
on ONE fact, the run's own heartbeat, and it is worth being plain about that
because this file used to promise a second one -- "no container is labelled with
its id" -- that `close_abandoned_runs` never checked and never could: liveness
here is REPORTED, because a second worker on a second host sees none of the
first host's containers and would call its live runs dead.

The rule is deliberately narrow, for the same reason the container reaper's is:
the dangerous half is not leaving a dead run open, it is killing a live one. So
the threshold is about missed REPORTS and never about elapsed work: a run is
closed only once whatever executes it has stopped saying it is alive for
`ABANDONED_AFTER` -- twenty consecutive missed beats. A run that keeps beating is
left alone however long it takes; nothing in THIS file stops an agent working
for an hour. (Its runtime may: a container runtime caps its own work at
`agent_max_steps * 60`. That is a cap on work, decided where the work is, and
not a liveness guess made from outside.)

Length is not evidence of death, and this comment used to claim otherwise: it
said the window was "far longer than any run this system produces". It is not,
and no such number can be named honestly -- run 019fc303 (Nora, real CRM work,
2026-08-02) was five minutes in and still going. The queue's reclaim was built on
that false sentence and killed her run for being slow.

The same window governs the queue: `oc8.runtime.worker` renews its claim on the
run's stream entry on the SAME beat and treats a claim unrenewed for this SAME
`ABANDONED_AFTER` as a dead worker. One window, two deciders -- and they are not
the same fact. This one knows whether the WORK is reporting; the queue's knows
whether a consumer is talking to Redis, which a partition can silence while the
work runs perfectly. So the queue's signal may not kill a run by itself:
`executor.recover_reclaimed` reads this heartbeat before it believes a reclaim.
Two facts, and they have to agree -- which is the rule this module has always
stated and, until 2026-08-02, only half kept.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import uuid
from collections.abc import AsyncIterator

from sqlalchemy import select, update

from oc8 import models as m
from oc8.db.session import tenant_session
from oc8.observability import record_run_outcome
from oc8.realtime.emit import publish_agent_status
from oc8.runtime.repository import RunRepository
from oc8.runtime.run_context import merge_context
from oc8.runtime.states import RunState
from oc8.triggers.scheduler import list_active_tenant_ids

logger = logging.getLogger(__name__)

#: How often an executing run reports that it is still alive -- to its database
#: row here, and to its stream entry in oc8.runtime.worker. One beat, both places.
HEARTBEAT_SECONDS = 30.0

#: How long that may stop before the run counts as abandoned. Twenty missed
#: beats: long enough that a slow database, a paused host or a stop-the-world
#: pause is never mistaken for a death, short enough that a run nothing is
#: executing any more frees its agent within a coffee break rather than at the
#: next deploy. Public because the worker applies the same window to the queue.
ABANDONED_AFTER = dt.timedelta(minutes=10)

#: Said on the run itself, because "failed" with no reason reads as the agent's
#: failure when it is the platform's.
REASON = (
    "abandoned: whatever was executing this stopped reporting in. "
    "The run was closed by the reconciler; nothing was lost that had been "
    "written, and the work can be started again."
)


async def close_abandoned_runs(
    *, now: dt.datetime | None = None
) -> list[uuid.UUID]:
    """Fail every run whose executor has gone silent. Returns the ids closed.

    Never raises: this runs on the worker's own timer, and one bad sweep must
    not stop it taking work.
    """
    cutoff = (now or dt.datetime.now(tz=dt.UTC)) - ABANDONED_AFTER

    closed: list[uuid.UUID] = []
    for tenant_id in await list_active_tenant_ids():
        try:
            async with tenant_session(tenant_id) as db:
                stale = (
                    (
                        await db.execute(
                            # Locked as they are selected, and a row somebody
                            # else already holds is left for them. Since
                            # 2026-08-02 this sweep and the queue's reclaim share
                            # one window, so for the first time they routinely
                            # agree about the same run at the same moment; with
                            # an unlocked read both saw `running`, both wrote,
                            # and the second error message erased the first.
                            # SKIP LOCKED rather than a wait, because a run
                            # somebody is already closing needs nothing from us.
                            select(m.AgentRun)
                            .where(
                                m.AgentRun.state == RunState.RUNNING.value,
                                m.AgentRun.updated_at < cutoff,
                            )
                            .with_for_update(skip_locked=True)
                        )
                    )
                    .scalars()
                    .all()
                )
                repo = RunRepository(db)
                for run in stale:
                    await merge_context(db, run, {"error": REASON})
                    agent = await db.get(m.Agent, run.agent_id)
                    if agent is not None and agent.status == "running":
                        # Freeing the agent is the point: while it reads as busy
                        # the office view lies, and a lead may hold work back.
                        agent.status = "idle"
                        await publish_agent_status(agent)
                    await repo.transition(run, RunState.FAILED)
                    record_run_outcome(RunState.FAILED.value)
                    closed.append(run.id)
        except Exception:
            logger.exception("reconciler: sweep failed for tenant %s", tenant_id)
    if closed:
        logger.warning(
            "reconciler: closed %d abandoned run(s): %s",
            len(closed),
            ", ".join(str(r) for r in closed),
        )
    return closed


__all__ = [
    "ABANDONED_AFTER",
    "HEARTBEAT_SECONDS",
    "REASON",
    "close_abandoned_runs",
    "heartbeat",
]


@contextlib.asynccontextmanager
async def heartbeat(
    *, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> AsyncIterator[None]:
    """Report that this run is still being executed, for as long as it is.

    Wrapped around the runtime call rather than left to each adapter: there are
    three of them, they run in different processes, and a liveness signal that
    one of them forgets is worse than none -- it would read as a death.

    Its own session per beat. The executor's session is handed to the runtime,
    which commits inside it, and two writers on one session is how a deadlock
    starts.
    """

    async def _beat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                async with tenant_session(tenant_id) as db:
                    await db.execute(
                        update(m.AgentRun)
                        .where(m.AgentRun.id == run_id)
                        .values(updated_at=dt.datetime.now(tz=dt.UTC))
                    )
                    await db.commit()
            except Exception:
                # One missed beat is not a death: the threshold is twenty of
                # them. Logging every hiccup at error level would drown the log
                # a real failure has to be found in.
                logger.debug("heartbeat missed for run %s", run_id, exc_info=True)

    task = asyncio.create_task(_beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
