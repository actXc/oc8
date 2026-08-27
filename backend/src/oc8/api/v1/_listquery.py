"""Shared building blocks for the uniform search/filter/group/pagination
contract (Design System Consistency plan, spec §1.1). Every list endpoint in
scope composes these three functions in the same order: search -> filters
(model-specific, applied by the caller) -> apply_group_order -> paginate.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql import Select


def apply_search(
    stmt: Select[Any],
    *,
    model: Any,
    columns: Sequence[InstrumentedAttribute[Any]],
    search: str | None,
) -> Select[Any]:
    """OR-ILIKE across the given columns. `model` is accepted (unused) so every
    call site is self-documenting about which model it's filtering."""
    if not search:
        return stmt
    pattern = f"%{search}%"
    return stmt.where(or_(*[col.ilike(pattern) for col in columns]))


def apply_group_order(
    stmt: Select[Any],
    *,
    model: Any,
    group_by: str | None,
    group_fields: dict[str, InstrumentedAttribute[Any]],
    default_order: InstrumentedAttribute[Any],
) -> Select[Any]:
    if group_by is None:
        return stmt.order_by(default_order)
    if group_by not in group_fields:
        # A caller-supplied value, so a caller error: 400, not the bare 500 a
        # ValueError leaking out of the route would produce. Every list route
        # in the plan's scope reaches this helper with an unvalidated query
        # parameter, so one refusal here covers all of them, and the message
        # names the fields that WOULD work instead of only the one that did not.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{group_by!r} is not a groupable field for {model.__name__}; "
                f"expected one of: {', '.join(sorted(group_fields))}"
            ),
        )
    return stmt.order_by(group_fields[group_by], default_order)


async def paginate(
    db: AsyncSession, stmt: Select[Any], *, limit: int, offset: int
) -> tuple[list[Any], int]:
    """Runs a COUNT over the filtered (pre-pagination) statement and a windowed
    fetch, in that order, over the same `stmt` -- so total_count always reflects
    every filter/search already applied, never the unfiltered table."""
    total = (
        await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ).scalar_one()
    rows = list((await db.execute(stmt.limit(limit).offset(offset))).scalars().all())
    return rows, int(total)
