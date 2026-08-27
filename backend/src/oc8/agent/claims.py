"""Two agents must not work the same record at once.

A department with two agents polling one queue hands both of them the same
oldest item. They read it in the same second, and the mission rule "claim the
ticket before you write anything" cannot close a race it does not know about --
by the time either writes, both have decided to. The customer gets two answers,
or two different ones.

So the first agent to CHANGE a record holds it for the rest of its run, and the
second is turned away with a sentence it can act on: who has it, since when, and
what to do instead. That last part matters. A bare denial reads as a broken tool
and the model retries; "take the next one" is a instruction it can follow.

Three properties this deliberately has:

* **The database decides.** A unique constraint, not a check-then-write in
  application code -- the other agent's write would land in between.
* **Reads never claim.** Two agents looking at the same queue is not a conflict;
  it is how a queue works. Only a change takes the record.
* **A dead holder loses it.** A claim whose run has finished is taken over
  rather than honoured, so a crash cannot wedge a ticket for ever. That is the
  same reasoning as the run reconciler, one level down.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.runtime.states import TERMINAL


@dataclass(frozen=True)
class Holder:
    """Who is working a record, for the sentence the other agent is told."""

    agent_name: str
    since: dt.datetime


def refusal(label: str, holder: Holder) -> str:
    """What the second agent is told. Says what to do next, deliberately.

    A refusal with no next step reads as a broken tool: the model retries the
    same call, burns its steps, and the run ends with nothing done.
    """
    when = holder.since.strftime("%H:%M")
    return (
        f"ERROR: {label} is already being worked on by {holder.agent_name} "
        f"(since {when}). Two answers to one request is worse than a late one. "
        f"Leave this one alone and take the next item instead."
    )


async def claim_record(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    entity: str,
    record_ref: str,
    run_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> Holder | None:
    """Take the record for this run, or say who has it.

    Returns None when the record is now this run's -- including when it already
    was. Returns the current holder when someone else has it and is still alive.
    """
    stmt = (
        pg_insert(m.RecordClaim)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            entity=entity,
            record_ref=record_ref,
            run_id=run_id,
            agent_id=agent_id,
        )
        .on_conflict_do_nothing(constraint="uq_record_claim")
        # RETURNING rather than rowcount: it says the same thing, works the same
        # on every driver, and is what the type of an execute() actually offers.
        .returning(m.RecordClaim.id)
    )
    if (await db.execute(stmt)).scalar_one_or_none() is not None:
        return None

    existing = (
        await db.execute(
            select(m.RecordClaim).where(
                m.RecordClaim.tenant_id == tenant_id,
                m.RecordClaim.entity == entity,
                m.RecordClaim.record_ref == record_ref,
            )
        )
    ).scalar_one_or_none()
    if existing is None:  # released between the insert and the read
        return await claim_record(
            db, tenant_id=tenant_id, entity=entity, record_ref=record_ref,
            run_id=run_id, agent_id=agent_id,
        )
    if existing.run_id == run_id:
        return None

    holder_run = await db.get(m.AgentRun, existing.run_id)
    alive = holder_run is not None and holder_run.state not in {s.value for s in TERMINAL}
    if not alive:
        # A crash must not wedge a record for ever. Same reasoning as the run
        # reconciler: a holder that no longer exists holds nothing.
        existing.run_id = run_id
        existing.agent_id = agent_id
        await db.flush()
        return None

    agent = await db.get(m.Agent, existing.agent_id)
    return Holder(
        agent_name=agent.name if agent is not None else "a colleague",
        since=existing.created_at,
    )


async def release_run_claims(
    db: AsyncSession, *, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> int:
    """Give up every record this run held. Returns how many."""
    rows = (
        (
            await db.execute(
                select(m.RecordClaim).where(
                    m.RecordClaim.tenant_id == tenant_id,
                    m.RecordClaim.run_id == run_id,
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        await db.delete(row)
    if rows:
        await db.flush()
    return len(rows)


async def held_by_run(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    entity: str,
    record_ref: str,
    run_id: uuid.UUID,
) -> bool:
    """Whether this run already holds this record.

    Asked before the blast-radius count, because acting twice on a record a run
    already has changes nothing about how far the run reaches -- and refusing it
    at the limit would stop an agent halfway through the one item it is allowed
    to work on.
    """
    return (
        await db.execute(
            select(m.RecordClaim.id).where(
                m.RecordClaim.tenant_id == tenant_id,
                m.RecordClaim.entity == entity,
                m.RecordClaim.record_ref == record_ref,
                m.RecordClaim.run_id == run_id,
            )
        )
    ).scalar_one_or_none() is not None
