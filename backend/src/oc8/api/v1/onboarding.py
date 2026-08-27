"""Two routes that end the gamified onboarding wizard's /welcome redirect
(design: gamified-onboarding-wizard-design.md). Deliberately two distinct
routes rather than one taking a status parameter -- the same "no parameter
here to get wrong" reasoning as useClarifications in hooks.ts, and it keeps a
client from ever being able to set onboarding_status back to 'pending'."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from oc8 import models as m
from oc8.api.deps import CurrentPrincipal, DbSession, require_permission
from oc8.authz.permissions import DEPARTMENT, MANAGE, perm
from oc8.schemas.base import CamelModel

router = APIRouter()


class OnboardingStatusDTO(CamelModel):
    onboarding_status: str


async def _set_status(db: DbSession, tenant_id: uuid.UUID, status: str) -> OnboardingStatusDTO:
    org = await db.get(m.Organization, tenant_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    org.onboarding_status = status
    await db.flush()
    return OnboardingStatusDTO(onboarding_status=status)


@router.post(
    "/onboarding/complete",
    response_model=OnboardingStatusDTO,
    dependencies=[Depends(require_permission(perm(DEPARTMENT, MANAGE)))],
)
async def complete_onboarding(db: DbSession, principal: CurrentPrincipal) -> OnboardingStatusDTO:
    return await _set_status(db, principal.tenant_id, "completed")


@router.post(
    "/onboarding/skip",
    response_model=OnboardingStatusDTO,
    dependencies=[Depends(require_permission(perm(DEPARTMENT, MANAGE)))],
)
async def skip_onboarding(db: DbSession, principal: CurrentPrincipal) -> OnboardingStatusDTO:
    return await _set_status(db, principal.tenant_id, "skipped")
