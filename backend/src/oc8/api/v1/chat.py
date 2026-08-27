"""Direct chat with one agent. See oc8.chat.service for the design: every
message is a real AgentRun (source="chat"), so guardrails/approvals apply
exactly as they do to an autonomous run."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from oc8 import models as m
from oc8.agents.repo import visible_agent
from oc8.api.deps import DbSession, require_departmental, require_permission
from oc8.authz.authority import authority_for_principal, tenant_wide_read
from oc8.authz.permissions import AGENT, RUN_START, VIEW, perm
from oc8.authz.scope import HumanActor
from oc8.chat.service import create_session, get_session, list_messages, list_sessions, send_message
from oc8.schemas.dto import ChatMessageDTO, ChatSessionDTO
from oc8.schemas.requests import CreateChatSessionRequest, SendChatMessageRequest

router = APIRouter()


def _session_dto(session: m.ChatSession) -> ChatSessionDTO:
    return ChatSessionDTO(
        id=str(session.id),
        agent_id=str(session.agent_id),
        title=session.title,
        created_at=session.created_at.isoformat(),
        last_message_at=session.last_message_at.isoformat() if session.last_message_at else None,
    )


def _message_dto(msg: m.ChatMessage) -> ChatMessageDTO:
    return ChatMessageDTO(
        id=str(msg.id),
        session_id=str(msg.session_id),
        role=msg.role,
        content=msg.content,
        run_id=str(msg.run_id) if msg.run_id else None,
        rendered_components=msg.rendered_components,
        created_at=msg.created_at.isoformat(),
    )


@router.get("/chat/sessions", response_model=list[ChatSessionDTO])
async def get_sessions(
    db: DbSession,
    actor: Annotated[HumanActor, Depends(require_departmental(perm(AGENT, VIEW)))],
    agent_id: uuid.UUID | None = None,
) -> list[ChatSessionDTO]:
    sessions = await list_sessions(
        db,
        tenant_id=actor.principal.tenant_id,
        member_id=actor.member.id,
        agent_id=agent_id,
    )
    return [_session_dto(s) for s in sessions]


@router.post("/chat/sessions", response_model=ChatSessionDTO, status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    request: Request,
    body: CreateChatSessionRequest,
    db: DbSession,
    actor: Annotated[HumanActor, Depends(require_departmental(perm(AGENT, VIEW)))],
) -> ChatSessionDTO:
    authority = await authority_for_principal(request, db, actor.principal)
    tenant_wide = tenant_wide_read(authority, perm(AGENT, VIEW))
    agent = await visible_agent(
        db, scope=actor.scope, tenant_wide=tenant_wide, agent_id=body.agent_id
    )
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
    session = await create_session(
        db,
        tenant_id=actor.principal.tenant_id,
        agent_id=body.agent_id,
        member_id=actor.member.id,
    )
    await db.commit()
    return _session_dto(session)


@router.get("/chat/sessions/{session_id}/messages", response_model=list[ChatMessageDTO])
async def get_messages(
    session_id: uuid.UUID,
    db: DbSession,
    actor: Annotated[HumanActor, Depends(require_departmental(perm(AGENT, VIEW)))],
) -> list[ChatMessageDTO]:
    session = await get_session(db, tenant_id=actor.principal.tenant_id, session_id=session_id)
    if session is None or session.member_id != actor.member.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chat session not found")
    messages = await list_messages(db, tenant_id=actor.principal.tenant_id, session_id=session_id)
    return [_message_dto(msg) for msg in messages]


@router.post(
    "/chat/sessions/{session_id}/messages",
    response_model=ChatMessageDTO,
    status_code=status.HTTP_201_CREATED,
    # RUN_START is a tenant-wide permission, not a departmental one -- it must
    # go through require_permission, never require_departmental (which would
    # silently grant it to anyone holding ANY department seat; see
    # DepartmentScope.holds_anywhere's own warning against exactly this).
    # `actor` below still resolves via require_departmental(AGENT, VIEW) for
    # its `member`/`scope`, since ownership of this session is checked against
    # that member id.
    dependencies=[Depends(require_permission(RUN_START))],
)
async def post_message(
    session_id: uuid.UUID,
    body: SendChatMessageRequest,
    db: DbSession,
    actor: Annotated[HumanActor, Depends(require_departmental(perm(AGENT, VIEW)))],
) -> ChatMessageDTO:
    session = await get_session(db, tenant_id=actor.principal.tenant_id, session_id=session_id)
    if session is None or session.member_id != actor.member.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chat session not found")
    user_message, _run = await send_message(
        db,
        session=session,
        tenant_id=actor.principal.tenant_id,
        message=body.message,
        originating_operator=actor.principal.subject,
    )
    return _message_dto(user_message)
