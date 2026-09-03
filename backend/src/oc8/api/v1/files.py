"""File attachments: upload endpoints scoped by owner (chat session or agent
instructions), plus the shared GET/DELETE by attachment id. See
docs/superpowers/specs/2026-09-03-chat-and-instruction-file-attachments-design.md's
Security Considerations -- no presigned URLs, every byte flows through here
after the same ownership/RLS checks every other protected resource uses."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi.responses import Response

from oc8 import models as m
from oc8.agents.repo import visible_agent
from oc8.api.deps import DbSession, require_departmental
from oc8.api.v1.chat import _owned_session
from oc8.authz.permissions import AGENT, VIEW, perm
from oc8.authz.scope import HumanActor
from oc8.knowledge.ingest import IngestionError, extract_text
from oc8.schemas.dto import FileAttachmentDTO
from oc8.storage import s3

router = APIRouter()

_MAX_BYTES = 25 * 1024 * 1024
_ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
    "text/plain",
    "text/markdown",
    "text/html",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
}
_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def _attachment_dto(row: m.FileAttachment) -> FileAttachmentDTO:
    return FileAttachmentDTO(
        id=str(row.id),
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        is_image=row.is_image,
        created_at=row.created_at.isoformat(),
    )


async def _store_upload(
    db: DbSession, *, tenant_id: uuid.UUID, owner_type: str, owner_id: uuid.UUID, file: UploadFile
) -> m.FileAttachment:
    if file.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"unsupported content type: {file.content_type!r}"
        )
    raw = await file.read()
    if len(raw) > _MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file exceeds 25 MB limit")

    is_image = file.content_type in _IMAGE_CONTENT_TYPES
    extracted_text: str | None = None
    if not is_image:
        import base64

        binary_types = {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        payload = (
            base64.b64encode(raw).decode()
            if file.content_type in binary_types
            else raw.decode("utf-8", errors="replace")
        )
        try:
            extracted_text = extract_text(content=payload, content_type=file.content_type)
        except IngestionError:
            extracted_text = None  # upload still succeeds -- see Global Constraints

    bucket_key = f"{tenant_id}/{owner_type}/{uuid.uuid4()}-{file.filename}"
    await s3.put_object(bucket_key, raw, file.content_type)

    row = m.FileAttachment(
        tenant_id=tenant_id,
        owner_type=owner_type,
        owner_id=owner_id,
        bucket_key=bucket_key,
        filename=file.filename or "upload",
        content_type=file.content_type,
        size_bytes=len(raw),
        extracted_text=extracted_text,
        is_image=is_image,
    )
    db.add(row)
    await db.flush()
    return row


@router.post(
    "/chat/sessions/{session_id}/attachments",
    response_model=FileAttachmentDTO,
    status_code=status.HTTP_201_CREATED,
)
async def upload_chat_attachment(
    request: Request,
    session_id: uuid.UUID,
    db: DbSession,
    actor: Annotated[HumanActor, Depends(require_departmental(perm(AGENT, VIEW)))],
    file: UploadFile,
) -> FileAttachmentDTO:
    await _owned_session(request, session_id, db, actor)
    row = await _store_upload(
        db,
        tenant_id=actor.principal.tenant_id,
        owner_type="chat_message",
        owner_id=session_id,
        file=file,
    )
    await db.commit()
    return _attachment_dto(row)


async def _owned_attachment(
    db: DbSession, actor: HumanActor, attachment_id: uuid.UUID
) -> m.FileAttachment:
    """Same ownership contract every other protected read enforces: either the
    row does not exist, or it exists but this caller may not see it -- both
    404 identically, so a caller can never learn which is true.

    The `chat_message` branch has two shapes because `FileAttachment.owner_id`
    changes meaning partway through a turn's life (see the design doc's Data
    Model section): it is a `ChatSession.id` from the moment a file is
    uploaded until the message that references it is actually sent, and a
    `ChatMessage.id` from `chat/service.py`'s `send_message` re-point onward.
    Try the session lookup directly first (the pre-send shape, and the
    overwhelmingly common one -- most attachments are read back moments after
    upload, before the surrounding message exists at all); only fall back to
    resolving through the message once that comes back empty, then re-run the
    exact same session-ownership check against the message's session. Either
    branch failing -- session/message missing, or found but not owned by this
    actor -- is the same 404 as above.
    """
    row = await db.get(m.FileAttachment, attachment_id)
    if row is None or row.tenant_id != actor.principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    if row.owner_type == "agent_instructions":
        agent = await visible_agent(db, scope=actor.scope, tenant_wide=False, agent_id=row.owner_id)
        if agent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
        return row

    from oc8.chat.service import get_session

    session = await get_session(db, tenant_id=actor.principal.tenant_id, session_id=row.owner_id)
    if session is None:
        message = await db.get(m.ChatMessage, row.owner_id)
        if message is None or message.tenant_id != actor.principal.tenant_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
        session = await get_session(
            db, tenant_id=actor.principal.tenant_id, session_id=message.session_id
        )
    if session is None or session.member_id != actor.member.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    return row


@router.get("/files/{attachment_id}")
async def download_file(
    attachment_id: uuid.UUID,
    db: DbSession,
    actor: Annotated[HumanActor, Depends(require_departmental(perm(AGENT, VIEW)))],
) -> Response:
    row = await _owned_attachment(db, actor, attachment_id)
    raw = await s3.get_object(row.bucket_key)
    return Response(content=raw, media_type=row.content_type)


@router.delete("/files/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    attachment_id: uuid.UUID,
    db: DbSession,
    actor: Annotated[HumanActor, Depends(require_departmental(perm(AGENT, VIEW)))],
) -> None:
    row = await _owned_attachment(db, actor, attachment_id)
    await s3.delete_object(row.bucket_key)
    await db.delete(row)
    await db.commit()
