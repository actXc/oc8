"""Tenant settings for the signed-in organization and approval policies."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from oc8 import models as m
from oc8.agents.hire import require_hire_approval, set_require_hire_approval
from oc8.api.deps import CurrentPrincipal, DbSession, require_permission
from oc8.audit import append_event
from oc8.authz.permissions import MANAGE, SETTINGS, VIEW, perm

router = APIRouter()


class HireApprovalSetting(BaseModel):
    enabled: bool


class OrganizationSettings(BaseModel):
    id: str
    name: str
    slug: str
    tier: str
    region: str


class OrganizationSettingsUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    region: str = Field(min_length=1, max_length=80)

    @field_validator("name", "region")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


def _organization_to_dto(organization: m.Organization) -> OrganizationSettings:
    return OrganizationSettings(
        id=str(organization.id),
        name=organization.name,
        slug=organization.slug,
        tier=organization.tier,
        region=organization.region,
    )


@router.get(
    "/settings/organization",
    response_model=OrganizationSettings,
    dependencies=[Depends(require_permission(perm(SETTINGS, VIEW)))],
)
async def get_organization_settings(
    db: DbSession, principal: CurrentPrincipal
) -> OrganizationSettings:
    organization = await db.get(m.Organization, principal.tenant_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="organization not found")
    return _organization_to_dto(organization)


@router.put(
    "/settings/organization",
    response_model=OrganizationSettings,
    dependencies=[Depends(require_permission(perm(SETTINGS, MANAGE)))],
)
async def put_organization_settings(
    body: OrganizationSettingsUpdate,
    db: DbSession,
    principal: CurrentPrincipal,
) -> OrganizationSettings:
    organization = await db.get(m.Organization, principal.tenant_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="organization not found")
    organization.name = body.name
    organization.region = body.region
    await append_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="operator",
        actor_id=None,
        category="admin",
        action="organization.settings.updated",
        resource={"organization_id": str(organization.id), "by": principal.subject},
        principal=principal,
    )
    await db.commit()
    return _organization_to_dto(organization)


@router.get(
    "/settings/hire-approval",
    response_model=HireApprovalSetting,
    dependencies=[Depends(require_permission(perm(SETTINGS, VIEW)))],
)
async def get_hire_approval(db: DbSession, principal: CurrentPrincipal) -> HireApprovalSetting:
    enabled = await require_hire_approval(db, tenant_id=principal.tenant_id)
    return HireApprovalSetting(enabled=enabled)


@router.put(
    "/settings/hire-approval",
    response_model=HireApprovalSetting,
    dependencies=[Depends(require_permission(perm(SETTINGS, MANAGE)))],
)
async def put_hire_approval(
    body: HireApprovalSetting,
    db: DbSession,
    principal: CurrentPrincipal,
) -> HireApprovalSetting:
    await set_require_hire_approval(db, tenant_id=principal.tenant_id, enabled=body.enabled)
    await db.commit()
    return HireApprovalSetting(enabled=body.enabled)
