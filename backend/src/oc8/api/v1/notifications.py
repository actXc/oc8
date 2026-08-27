# backend/src/oc8/api/v1/notifications.py
"""Self-service Web Push subscription management. Every route here is
`unguarded`, not permission-gated: an operator manages only their own
device subscriptions, the same trust level as reading one's own profile
(see GET /me, api/v1/auth.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from oc8 import models as m
from oc8.api.deps import CurrentPrincipal, DbSession, unguarded
from oc8.auth import Principal
from oc8.authz.scope import scope_for_principal
from oc8.config import get_settings
from oc8.schemas.dto import VapidPublicKeyDTO
from oc8.schemas.requests import PushSubscribeRequest

router = APIRouter()

_UNGUARDED_REASON = (
    "manages only the caller's own device subscriptions -- the same trust "
    "level as reading one's own profile"
)


async def _member_for(db: DbSession, principal: Principal) -> m.OrgMember:
    """The caller's own `OrgMember` row, or a clean 403.

    `scope_for_principal(..., upsert=True)` raises `PermissionError` for a
    principal that cannot stand in a department at all (a plugin token; an
    agent token never reaches the operator API) -- see GET /me's identical
    catch (api/v1/auth.py:216-230) for the precedent. Push subscriptions are
    a per-human-operator concept, so such a caller gets a 403, not a 500.
    """
    try:
        member, _scope = await scope_for_principal(db, principal, upsert=True)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this caller cannot hold a push subscription",
        ) from exc
    assert member is not None  # upsert=True raises before returning None
    return member


@router.get(
    "/notifications/push/vapid-public-key",
    response_model=VapidPublicKeyDTO,
    dependencies=[Depends(unguarded(_UNGUARDED_REASON))],
)
async def get_vapid_public_key(principal: CurrentPrincipal) -> VapidPublicKeyDTO:
    return VapidPublicKeyDTO(public_key=get_settings().vapid_public_key)


@router.post(
    "/notifications/push/subscriptions",
    status_code=204,
    dependencies=[Depends(unguarded(_UNGUARDED_REASON))],
)
async def subscribe(
    body: PushSubscribeRequest, principal: CurrentPrincipal, db: DbSession
) -> Response:
    member = await _member_for(db, principal)
    # `tenant_id` is spelled out rather than left to RLS. Uniqueness on this
    # table is `(tenant_id, endpoint)` -- the same browser legitimately holds
    # one row per tenant its operator belongs to -- so the row this lookup must
    # find is precisely the one in THIS tenant. RLS already restricts the
    # session to that, but a lookup whose correctness now depends on the tenant
    # half of a composite key should say so where it is read, not two layers
    # down.
    result = await db.execute(
        select(m.PushSubscription).where(
            m.PushSubscription.tenant_id == principal.tenant_id,
            m.PushSubscription.endpoint == body.endpoint,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        if existing.member_id != member.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="this push endpoint is already registered to another member",
            )
        existing.p256dh = body.keys.p256dh
        existing.auth = body.keys.auth
    else:
        db.add(
            m.PushSubscription(
                tenant_id=principal.tenant_id,
                member_id=member.id,
                endpoint=body.endpoint,
                p256dh=body.keys.p256dh,
                auth=body.keys.auth,
            )
        )
    await db.commit()
    return Response(status_code=204)


@router.delete(
    "/notifications/push/subscriptions",
    status_code=204,
    dependencies=[Depends(unguarded(_UNGUARDED_REASON))],
)
async def unsubscribe(endpoint: str, principal: CurrentPrincipal, db: DbSession) -> Response:
    member = await _member_for(db, principal)
    result = await db.execute(
        select(m.PushSubscription).where(
            m.PushSubscription.endpoint == endpoint,
            m.PushSubscription.member_id == member.id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        await db.delete(existing)
        await db.commit()
    return Response(status_code=204)
