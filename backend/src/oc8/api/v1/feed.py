"""Activity feed, approvals inbox, and usage rollups (read)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from oc8 import models as m
from oc8.api.deps import DbSession, require_departmental, require_permission
from oc8.api.v1._serializers import activity_to_dto, approval_to_dto, resolve_approval_names
from oc8.approvals.repo import DEFAULT_LIMIT, visible_approvals
from oc8.authz.permissions import BUDGET, RUN, VIEW, perm
from oc8.authz.scope import APPROVAL_VIEW, HumanActor
from oc8.metering.pricing import active_price_rows, cost_micros_from_price, price_as_of
from oc8.schemas.dto import ActivityDTO, ApprovalDTO, PrincipalUsageDTO

router = APIRouter()


@router.get(
    "/activity",
    response_model=list[ActivityDTO],
    dependencies=[Depends(require_permission(perm(RUN, VIEW)))],
)
async def list_activity(
    db: DbSession,
    limit: int = 50,
    agent_id: uuid.UUID | None = Query(default=None, alias="agentId"),
    before: uuid.UUID | None = None,
) -> list[ActivityDTO]:
    """Newest first, optionally for one agent, optionally starting after a row.

    `agent_id` is served here rather than filtered in the browser: a global
    page of 50 can contain nothing at all from the agent whose screen you are
    on, so its feed looked empty while its history sat just past the cut.

    `before` takes the id of the last row you were given, not an offset. Ids are
    time-ordered (uuid7) and rows only ever arrive at the newest end, so paging
    by id cannot skip or repeat a row the way OFFSET does when the feed grows
    between two requests.
    """
    limit = max(1, min(limit, 200))
    query = select(m.ActivityEvent)
    if agent_id is not None:
        query = query.where(m.ActivityEvent.agent_id == agent_id)
    if before is not None:
        query = query.where(m.ActivityEvent.id < before)
    rows = (
        (
            await db.execute(
                # ts DESC is what a reader means by "newest"; id breaks the tie,
                # and is also what `before` pages on, so the two must agree.
                query.order_by(m.ActivityEvent.ts.desc(), m.ActivityEvent.id.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [activity_to_dto(e) for e in rows]


@router.get("/approvals", response_model=list[ApprovalDTO])
async def list_approvals(
    db: DbSession,
    # A ROUTE PARAMETER and not `dependencies=[...]`: the body needs the actor,
    # and a dependency in the decorator list is resolved and then thrown away.
    actor: Annotated[HumanActor, Depends(require_departmental(APPROVAL_VIEW))],
    status: str = "pending",
    department_id: uuid.UUID | None = Query(default=None, alias="departmentId"),
    limit: int = DEFAULT_LIMIT,
) -> list[ApprovalDTO]:
    """The approvals this caller may see -- not the tenant's.

    Until this slice the query was `WHERE status = :status` and nothing else, so
    a company that gave its Head of Sales an account gave him every pending
    approval in it: Engineering's production access request, its titles and its
    amounts. They are not filtered out in the browser now; they are never
    fetched.

    `departmentId` INTERSECTS the scope and never widens it -- it is the picker
    an admin gets over departments he can already see, so asking for somebody
    else's returns `[]` rather than 403 (a 403 would answer "does that
    department exist"). `limit` is clamped in the repository.

    A seat-holder whose queue is quiet gets 200 and `[]`; somebody with no seat
    anywhere is refused at the gate with a sentence telling him to ask an
    administrator. Those two states are the same blank screen today, and "the
    system is broken" must not look like "nobody has added me yet" (§7).
    """
    rows = await visible_approvals(
        db, actor=actor, status=status, department_id=department_id, limit=limit
    )
    # Four batched lookups for the whole page, not four per row: the detail pane
    # names the agent, the department and the task, and resolving those while
    # rendering is how a queue of a hundred becomes four hundred and one requests.
    names = await resolve_approval_names(db, rows)
    return [approval_to_dto(a, names) for a in rows]


@router.get(
    "/usage",
    response_model=list[PrincipalUsageDTO],
    dependencies=[Depends(require_permission(perm(BUDGET, VIEW)))],
)
async def usage(db: DbSession, group_by: str = "department") -> list[PrincipalUsageDTO]:
    col = (
        m.TokenUsageRecord.department_id
        if group_by == "department"
        else m.TokenUsageRecord.agent_id
    )
    day = func.date_trunc("day", m.TokenUsageRecord.ts)
    rows = (
        await db.execute(
            select(
                col,
                day,
                m.TokenUsageRecord.provider,
                m.TokenUsageRecord.model,
                func.coalesce(func.sum(m.TokenUsageRecord.tokens_in), 0),
                func.coalesce(func.sum(m.TokenUsageRecord.tokens_out), 0),
                func.coalesce(func.sum(m.TokenUsageRecord.saved_tokens_in), 0),
                func.coalesce(func.sum(m.TokenUsageRecord.saved_tokens_out), 0),
            ).group_by(col, day, m.TokenUsageRecord.provider, m.TokenUsageRecord.model)
        )
    ).all()

    now = datetime.now(UTC)
    price_rows = await active_price_rows(db, as_of=now)

    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "tokens_in": 0,
            "tokens_out": 0,
            "cost": 0,
            "saved_tokens_in": 0,
            "saved_tokens_out": 0,
            "saved_cost": 0,
        }
    )
    for group, bucket_day, provider, model, tokens_in, tokens_out, saved_in, saved_out in rows:
        key = str(group) if group else "unassigned"
        # `bucket_day` is the midnight start of this bucket's day, but a price
        # row's effective_from is a real wall-clock timestamp -- a price edited
        # at, say, 14:00 today has an effective_from *after* today's midnight,
        # so comparing against the bucket's start would make it (and any price
        # from "today") never match its own day's usage. Compare against the
        # end of the bucket's day instead: any price effective at any point
        # during that day applies to that day's report, matching the day-level
        # granularity this endpoint already chose.
        price = price_as_of(price_rows, provider, model, bucket_day + timedelta(days=1))
        cost = cost_micros_from_price(price, int(tokens_in), int(tokens_out)) if price else 0
        saved_cost = cost_micros_from_price(price, int(saved_in), int(saved_out)) if price else 0
        totals[key]["tokens_in"] += int(tokens_in)
        totals[key]["tokens_out"] += int(tokens_out)
        totals[key]["cost"] += cost
        totals[key]["saved_tokens_in"] += int(saved_in)
        totals[key]["saved_tokens_out"] += int(saved_out)
        totals[key]["saved_cost"] += saved_cost

    return [
        PrincipalUsageDTO(
            group=group,
            tokens_in=v["tokens_in"],
            tokens_out=v["tokens_out"],
            provider_cost_micros=v["cost"],
            saved_tokens_in=v["saved_tokens_in"],
            saved_tokens_out=v["saved_tokens_out"],
            saved_cost_micros=v["saved_cost"],
        )
        for group, v in totals.items()
    ]
