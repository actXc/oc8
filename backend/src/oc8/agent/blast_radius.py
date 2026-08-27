"""How much one run may change, whatever it has been talked into.

A ticket body is written by a stranger and lands in the model's context beside
oc8's own instructions. There is no reliable way to tell an instruction from a
quoted one, and pattern-matching for "ignore your instructions" is evaded in a
sentence while catching real customers who write "please ignore my last mail".

So the question is not how to detect the attack. It is how to survive being
fooled -- which is also the only defence that holds against the attack nobody
has thought of yet. The frame already bounds WHAT an agent may do. This bounds
HOW MUCH: a run may change a handful of records, not the whole queue.

That turns "close every ticket" into "one ticket mishandled, then a stop and a
human who has been told". The counter costs nothing extra: the records a run
holds a claim on ARE the records it has changed, so the claims are the ledger.

It is the missing sibling of the budgets that already exist for tokens and
money (metering/budget.py). Those cap what a run may SPEND; nothing capped what
it may DO.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m

#: Records one run may change when the department says nothing. Deliberately
#: enforced by DEFAULT rather than opt-in: a limit nobody switched on protects
#: nobody. Five is generous for the work these agents do -- a support run
#: touches its ticket and perhaps one issue -- and small enough that a run
#: marching through a queue hits it almost immediately.
DEFAULT_RECORDS_PER_RUN = 5

#: What a department writes in its frame to widen or lift it:
#:
#:     [plugin.department_template.frame.limits]
#:     records_per_run = 20   # 0 means no limit
LIMITS_KEY = "limits"
RECORDS_PER_RUN = "records_per_run"


def records_per_run(frame: dict[str, Any] | None) -> int:
    """The ceiling for this department. 0 means no ceiling."""
    limits = (frame or {}).get(LIMITS_KEY)
    if not isinstance(limits, dict) or RECORDS_PER_RUN not in limits:
        return DEFAULT_RECORDS_PER_RUN
    try:
        value = int(limits[RECORDS_PER_RUN])
    except (TypeError, ValueError):
        return DEFAULT_RECORDS_PER_RUN
    return max(0, value)


async def records_touched(
    db: AsyncSession, *, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> int:
    """How many distinct records this run has already changed."""
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(m.RecordClaim)
                .where(
                    m.RecordClaim.tenant_id == tenant_id,
                    m.RecordClaim.run_id == run_id,
                )
            )
        ).scalar_one()
    )


def refusal(touched: int, limit: int) -> str:
    """What the model is told. Names the cause, not just the rule.

    A model that has been steered into a sweep will otherwise read a bare denial
    as a broken tool and keep trying. Telling it what happened is also the only
    honest thing to do: it may not have done anything wrong.
    """
    return (
        f"ERROR: this run has already changed {touched} records, which is the "
        f"limit of {limit} for a single run. No further changes will be made and "
        f"the run ends here. A colleague has been asked to look at it. If this "
        f"work is legitimate, they can let it continue -- do not try to work "
        f"around the limit."
    )


def alarm(agent_name: str, touched: int, limit: int, entity: str, ref: str) -> str:
    return (
        f"{agent_name} changed {touched} records in a single run and was stopped "
        f"at the limit of {limit}. The refused action was on {entity} {ref}. "
        f"An agent that sweeps through a queue when it normally handles one item "
        f"has usually been told to by something it read. Check what it changed "
        f"before letting it continue."
    )
