"""Company backup: export a tenant to one archive, preview a restore, or
restore in place from an uploaded archive (design doc §3, §5, §8).

Every write below happens inside the caller's own RLS-bound session
(`DbSession`), and this module -- not `oc8.backup.archive`/`oc8.backup.service`
-- owns the transaction boundary: those modules deliberately never commit
(see their own docstrings), because a mid-service commit would unbind the
Postgres RLS GUC (`app.tenant_id`) inside the one function that deletes whole
tables. `restore_backup` below commits explicitly, matching
`settings.py::put_organization_settings`; `export_backup`/`preview_backup`
write nothing and commit nothing, matching `settings.py`'s GET routes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from oc8 import models as m
from oc8.api.deps import CurrentPrincipal, DbSession, require_permission
from oc8.authz.permissions import BACKUP_EXPORT, BACKUP_RESTORE
from oc8.backup.archive import export_archive
from oc8.backup.errors import BackupError
from oc8.backup.service import preview_restore, restore_from_archive

router = APIRouter()


class BackupExportRequest(BaseModel):
    #: Encrypts every stored credential into the archive under this
    #: passphrase (design doc §3). Omitted entirely: the archive carries no
    #: `secrets.json` at all, matching `export_archive`'s own contract.
    passphrase: str | None = None


class BackupPreviewDTO(BaseModel):
    manifest: dict[str, Any]
    table_counts: dict[str, dict[str, int]]
    has_secrets: bool
    problems: list[str]


class BackupRestoreResultDTO(BaseModel):
    tables: dict[str, int]
    secrets_restored: int
    excluded: list[str]


@router.post(
    "/backup/export",
    dependencies=[Depends(require_permission(BACKUP_EXPORT))],
)
async def export_backup(
    body: BackupExportRequest, db: DbSession, principal: CurrentPrincipal
) -> Response:
    filename, data = await export_archive(
        db, tenant_id=principal.tenant_id, passphrase=body.passphrase
    )
    return Response(
        content=data,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/backup/preview",
    response_model=BackupPreviewDTO,
    dependencies=[Depends(require_permission(BACKUP_RESTORE))],
)
async def preview_backup(
    db: DbSession, principal: CurrentPrincipal, file: UploadFile = File(...)
) -> BackupPreviewDTO:
    data = await file.read()
    try:
        preview = await preview_restore(db, tenant_id=principal.tenant_id, archive_bytes=data)
    except BackupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return BackupPreviewDTO(
        manifest=preview.manifest,
        table_counts=preview.table_counts,
        has_secrets=preview.has_secrets,
        problems=preview.problems,
    )


@router.post(
    "/backup/restore",
    response_model=BackupRestoreResultDTO,
    dependencies=[Depends(require_permission(BACKUP_RESTORE))],
)
async def restore_backup(
    db: DbSession,
    principal: CurrentPrincipal,
    file: UploadFile = File(...),
    confirm_name: str = Form(...),
    passphrase: str | None = Form(None),
) -> BackupRestoreResultDTO:
    # This deletes every agent, run and knowledge chunk the company has, so
    # typing the company's current name is the proportionate confirmation
    # gate -- checked, and refused, BEFORE the archive is even read.
    organization = await db.get(m.Organization, principal.tenant_id)
    if organization is None or confirm_name != organization.name:
        raise HTTPException(status_code=422, detail="confirm_name does not match")
    data = await file.read()
    try:
        result = await restore_from_archive(
            db,
            tenant_id=principal.tenant_id,
            archive_bytes=data,
            passphrase=passphrase,
            actor_type="operator",
            actor_id=None,
            # Names the human in the audit event `restore_from_archive`
            # appends, rather than letting `resolve_responsible` fall back
            # to the tenant (see that function's docstring).
            principal=principal,
        )
    except BackupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    return BackupRestoreResultDTO(
        tables=result.tables, secrets_restored=result.secrets_restored, excluded=result.excluded
    )
