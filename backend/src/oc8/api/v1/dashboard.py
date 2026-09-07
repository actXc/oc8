"""Per-member "My Work" grid layout -- purely personal, never shared.

Every route here is `unguarded`, not permission-gated: a member manages only
their own dashboard arrangement, the same trust level as reading one's own
profile (see `notifications.py`'s identical framing for push subscriptions).
No `member_id` path or query parameter exists on any route, so there is no
ownership check to write or forget: the row a request can ever touch is
fixed by the caller's own identity token.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from oc8 import models as m
from oc8.api.deps import CurrentPrincipal, DbSession, unguarded
from oc8.auth import Principal
from oc8.authz.scope import scope_for_principal
from oc8.schemas.dto import DashboardLayoutDTO, DashboardTemplateDTO, WidgetInstanceDTO
from oc8.schemas.requests import PutDashboardLayoutRequest

router = APIRouter()

_UNGUARDED_REASON = (
    "manages only the caller's own dashboard layout -- the same trust level "
    "as reading one's own profile"
)

_TEMPLATES: list[DashboardTemplateDTO] = [
    DashboardTemplateDTO(
        id="focus-chat",
        name={"en": "Focus Chat", "de": "Fokus-Chat"},
        widgets=[
            WidgetInstanceDTO(id="t1-chat", type="chat", x=0, y=0, w=8, h=8, config={}),
            WidgetInstanceDTO(id="t1-approvals", type="approvals", x=8, y=0, w=4, h=8, config={}),
        ],
    ),
    DashboardTemplateDTO(
        id="overview",
        name={"en": "Overview", "de": "Überblick"},
        widgets=[
            WidgetInstanceDTO(id="t2-approvals", type="approvals", x=0, y=0, w=6, h=5, config={}),
            WidgetInstanceDTO(id="t2-reports", type="reports", x=6, y=0, w=6, h=5, config={}),
            WidgetInstanceDTO(id="t2-budget", type="budget", x=0, y=5, w=6, h=3, config={}),
            WidgetInstanceDTO(id="t2-activity", type="activity", x=6, y=5, w=6, h=3, config={}),
        ],
    ),
    DashboardTemplateDTO(
        id="mixed",
        name={"en": "Mixed", "de": "Gemischt"},
        widgets=[
            WidgetInstanceDTO(id="t3-chat1", type="chat", x=0, y=0, w=6, h=5, config={}),
            WidgetInstanceDTO(id="t3-chat2", type="chat", x=6, y=0, w=6, h=5, config={}),
            WidgetInstanceDTO(id="t3-approvals", type="approvals", x=0, y=5, w=6, h=3, config={}),
            WidgetInstanceDTO(id="t3-reports", type="reports", x=6, y=5, w=6, h=3, config={}),
        ],
    ),
]


async def _member_for(db: DbSession, principal: Principal) -> m.OrgMember:
    """The caller's own `OrgMember` row, or a clean 403 -- see
    `notifications.py::_member_for` for the identical rationale."""
    try:
        member, _scope = await scope_for_principal(db, principal, upsert=True)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this caller cannot hold a dashboard layout",
        ) from exc
    assert member is not None  # upsert=True raises before returning None
    return member


@router.get(
    "/dashboard/layout",
    response_model=DashboardLayoutDTO | None,
    dependencies=[Depends(unguarded(_UNGUARDED_REASON))],
)
async def get_layout(principal: CurrentPrincipal, db: DbSession) -> DashboardLayoutDTO | None:
    member = await _member_for(db, principal)
    result = await db.execute(
        select(m.MemberDashboardLayout).where(m.MemberDashboardLayout.member_id == member.id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return DashboardLayoutDTO(widgets=row.widgets, template_id=row.template_id)


@router.put(
    "/dashboard/layout",
    response_model=DashboardLayoutDTO,
    dependencies=[Depends(unguarded(_UNGUARDED_REASON))],
)
async def put_layout(
    body: PutDashboardLayoutRequest, principal: CurrentPrincipal, db: DbSession
) -> DashboardLayoutDTO:
    member = await _member_for(db, principal)
    widgets_json = [w.model_dump(mode="json") for w in body.widgets]
    # Upsert rather than select-then-insert: two concurrent first-saves could
    # otherwise both see no existing row and race on the INSERT against the
    # table's UNIQUE(member_id) constraint, with the loser hitting an
    # unhandled unique-violation.
    stmt = (
        pg_insert(m.MemberDashboardLayout)
        .values(
            tenant_id=principal.tenant_id,
            member_id=member.id,
            widgets=widgets_json,
            template_id=body.template_id,
        )
        .on_conflict_do_update(
            index_elements=["member_id"],
            set_={
                "widgets": widgets_json,
                "template_id": body.template_id,
                "updated_at": func.now(),
            },
        )
    )
    await db.execute(stmt)
    await db.commit()
    # `widgets_json`, not `body.widgets` directly: the request's widgets are
    # `WidgetInstanceDTO` (the write path's `Literal`-typed model), while
    # `DashboardLayoutDTO` now expects the read path's `WidgetInstanceReadDTO`
    # (plain `str` type) -- passing an instance of the wrong model class
    # raises a validation error instead of coercing.
    return DashboardLayoutDTO(widgets=widgets_json, template_id=body.template_id)


@router.get(
    "/dashboard/templates",
    response_model=list[DashboardTemplateDTO],
    dependencies=[Depends(unguarded("returns the static template catalogue, no tenant data"))],
)
async def get_templates() -> list[DashboardTemplateDTO]:
    return _TEMPLATES
