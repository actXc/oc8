"""Operator-gated secret store CRUD (tech-spec §12.3).

Metadata only, ever: `SecretDTO` carries no `value` field and there is no
resolve-over-HTTP endpoint. `resolve_secret` (oc8.secrets.service) is
server-side only, consumed by other backend modules that need the plaintext
at call time -- never exposed through this API.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from oc8 import models as m
from oc8.api.deps import CurrentPrincipal, DbSession, require_permission
from oc8.api.v1.totp import SECRET_KIND as _TOTP_SECRET_KIND
from oc8.authz.permissions import MANAGE, SECRET, VIEW, perm
from oc8.schemas.dto import SecretDTO
from oc8.schemas.requests import CreateSecretRequest
from oc8.secrets.keyprovider import SecretStoreUnavailable
from oc8.secrets.service import store_secret

router = APIRouter()

# Mirrors the org_admin gate used by other sensitive mutating admin endpoints
# (catalog.py's model-config CRUD, settings.py's hire-approval toggle):
# require_role("org_admin") 403s any principal whose role isn't org_admin.

#: `store_secret` upserts by `(tenant_id, name)`, and this CRUD lets any
#: `secret:manage` caller pick an ARBITRARY `name` -- so, without this
#: refusal, `POST /secrets` with `name="totp:<member_id>"` would silently
#: overwrite the vault row `totp.py`'s `/auth/totp/confirm` created (an
#: attacker-chosen TOTP secret, i.e. second-factor impersonation once
#: Task 6's `/verify` reads it), and `DELETE /secrets/{id}` would silently
#: lock that member out. Both must go ONLY through the TOTP enrollment
#: flow that owns them.
_TOTP_KIND_REFUSED_REASON = (
    "TOTP secrets are managed only through the TOTP enrollment flow, not the generic secrets API"
)


def _to_dto(sec: m.Secret) -> SecretDTO:
    return SecretDTO(
        id=str(sec.id),
        name=sec.name,
        kind=sec.kind,
        key_version=sec.key_version,
        created_at=str(sec.created_at),
    )


@router.post(
    "/secrets",
    response_model=SecretDTO,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(perm(SECRET, MANAGE)))],
)
async def create_secret(
    body: CreateSecretRequest,
    db: DbSession,
    principal: CurrentPrincipal,
) -> SecretDTO:
    # Refuse outright if the caller asked for kind="totp" ...
    if body.kind == _TOTP_SECRET_KIND:
        raise HTTPException(status.HTTP_403_FORBIDDEN, _TOTP_KIND_REFUSED_REASON)
    # ... AND refuse an upsert that targets a name a TOTP enrollment already
    # owns, regardless of what kind THIS request asked for -- `store_secret`
    # upserts by `(tenant_id, name)` alone, so a caller who left `kind` at
    # its "generic" default (or picked any kind other than "totp") could
    # otherwise overwrite the existing "totp"-kind row anyway, replacing its
    # ciphertext AND downgrading its kind, right past the check above.
    existing = (
        await db.execute(
            select(m.Secret.kind).where(
                m.Secret.tenant_id == principal.tenant_id, m.Secret.name == body.name
            )
        )
    ).scalar_one_or_none()
    if existing == _TOTP_SECRET_KIND:
        raise HTTPException(status.HTTP_403_FORBIDDEN, _TOTP_KIND_REFUSED_REASON)
    try:
        sec = await store_secret(
            db,
            tenant_id=principal.tenant_id,
            name=body.name,
            value=body.value,
            kind=body.kind,
        )
    except SecretStoreUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    await db.commit()
    return _to_dto(sec)


@router.get(
    "/secrets",
    response_model=list[SecretDTO],
    dependencies=[Depends(require_permission(perm(SECRET, VIEW)))],
)
async def list_secrets(
    db: DbSession,
    principal: CurrentPrincipal,
) -> list[SecretDTO]:
    # `kind="totp"` rows are excluded, not merely undeletable here: listing a
    # member's TOTP secret ref alongside every other credential this CRUD can
    # delete is itself part of what F1 flagged -- an operator with
    # `secret:view` (but not `secret:manage`, or one who simply never reads
    # the enrollment flow's own docs) has no way to tell this ref apart from
    # an ordinary revocable API key.
    rows = (
        (
            await db.execute(
                select(m.Secret)
                .where(m.Secret.kind != _TOTP_SECRET_KIND)
                .order_by(m.Secret.name)
            )
        )
        .scalars()
        .all()
    )
    return [_to_dto(s) for s in rows]


@router.delete(
    "/secrets/{secret_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(perm(SECRET, MANAGE)))],
)
async def delete_secret_endpoint(
    secret_id: uuid.UUID,
    db: DbSession,
    principal: CurrentPrincipal,
) -> None:
    row = await db.get(m.Secret, secret_id)
    if row is None or row.tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "secret not found")
    if row.kind == _TOTP_SECRET_KIND:
        raise HTTPException(status.HTTP_403_FORBIDDEN, _TOTP_KIND_REFUSED_REASON)
    await db.delete(row)
    await db.commit()
