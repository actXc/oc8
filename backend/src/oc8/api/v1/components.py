"""Manage ComponentGrant rows -- which agent or department may render which
governed UI component (oc8.agent.components.COMPONENT_CATALOG). Mirrors
/knowledge/grants (api/v1/knowledge.py's create_grant) almost exactly: same
department/agent grantee shape, same idempotent-create, same "list once,
filter client-side" contract, since the catalogue here is a short, fixed
list of keys rather than a tenant-created resource with its own detail page.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from oc8 import models as m
from oc8.agent.components import COMPONENT_CATALOG
from oc8.api.deps import CurrentPrincipal, DbSession, require_permission
from oc8.authz.permissions import AGENT, MANAGE, perm
from oc8.schemas.dto import ComponentGrantDTO
from oc8.schemas.requests import CreateComponentGrantRequest

router = APIRouter()


def _to_dto(grant: m.ComponentGrant) -> ComponentGrantDTO:
    return ComponentGrantDTO(
        id=str(grant.id),
        component_key=grant.component_key,
        grantee_type=grant.grantee_type,
        grantee_id=str(grant.grantee_id),
    )


@router.get(
    "/components/grants",
    response_model=list[ComponentGrantDTO],
    dependencies=[Depends(require_permission(perm(AGENT, MANAGE)))],
)
async def list_component_grants(db: DbSession) -> list[ComponentGrantDTO]:
    grants = (await db.execute(select(m.ComponentGrant))).scalars().all()
    return [_to_dto(g) for g in grants]


@router.post(
    "/components/grants",
    response_model=ComponentGrantDTO,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(perm(AGENT, MANAGE)))],
)
async def create_component_grant(
    body: CreateComponentGrantRequest, db: DbSession, principal: CurrentPrincipal
) -> ComponentGrantDTO:
    if body.component_key not in COMPONENT_CATALOG:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown component")

    grantee: m.Department | m.Agent | None
    if body.grantee_type == "department":
        grantee = await db.get(m.Department, body.grantee_id)
    else:
        grantee = await db.get(m.Agent, body.grantee_id)
    if grantee is None or grantee.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{body.grantee_type} not found")

    existing = (
        await db.execute(
            select(m.ComponentGrant).where(
                m.ComponentGrant.component_key == body.component_key,
                m.ComponentGrant.grantee_type == body.grantee_type,
                m.ComponentGrant.grantee_id == body.grantee_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _to_dto(existing)

    grant = m.ComponentGrant(
        tenant_id=principal.tenant_id,
        component_key=body.component_key,
        grantee_type=body.grantee_type,
        grantee_id=body.grantee_id,
    )
    db.add(grant)
    await db.flush()
    return _to_dto(grant)


@router.delete(
    "/components/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(perm(AGENT, MANAGE)))],
)
async def delete_component_grant(grant_id: uuid.UUID, db: DbSession) -> None:
    grant = await db.get(m.ComponentGrant, grant_id)
    if grant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "grant not found")
    await db.delete(grant)
