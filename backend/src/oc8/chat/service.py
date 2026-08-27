"""Direct chat with a single agent.

Deliberately NOT a parallel execution engine: every user message becomes a
real `AgentRun` (`source="chat"`), enqueued through the exact same
`oc8.runtime.intake.enqueue_run` funnel every other run source uses. That
means a chat turn gets the department frame's guardrails, tool policy and
approval-suspend/resume for free -- a `delete_record` call typed into chat
pauses in "My work" exactly like one from an autonomous run, because as far
as the executor is concerned it IS one.

All turns in one `ChatSession` share a single `Task` (`ChatSession.task_id`,
set from the first run's resolved `task_id` and passed back into every later
`enqueue_run` call) -- see `engine.open_run_task`'s `resume_task_id`
handling, which is what makes passing an already-set `AgentRun.task_id`
reuse that task instead of opening a new one. One chat session is one item
on the department board, not one per message.

`record_assistant_reply` is called from `oc8.runtime.executor` the moment a
`source="chat"` run reaches a terminal state -- it is the one place a run's
outcome becomes the durable transcript message the Chat UI reads.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.runtime.intake import enqueue_run

#: How many prior turns to fold into the next run's `task` text. A chat is
#: interactive, not a long autonomous investigation -- this keeps the prompt
#: bounded without needing a separate summarization step for v1.
_TRANSCRIPT_TURNS = 20


async def create_session(
    db: AsyncSession, *, tenant_id: uuid.UUID, agent_id: uuid.UUID, member_id: uuid.UUID
) -> m.ChatSession:
    session = m.ChatSession(tenant_id=tenant_id, agent_id=agent_id, member_id=member_id)
    db.add(session)
    await db.flush()
    return session


async def get_session(
    db: AsyncSession, *, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> m.ChatSession | None:
    return (
        await db.execute(
            select(m.ChatSession).where(
                m.ChatSession.tenant_id == tenant_id, m.ChatSession.id == session_id
            )
        )
    ).scalar_one_or_none()


async def list_sessions(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    member_id: uuid.UUID,
    agent_id: uuid.UUID | None = None,
) -> list[m.ChatSession]:
    stmt = select(m.ChatSession).where(
        m.ChatSession.tenant_id == tenant_id, m.ChatSession.member_id == member_id
    )
    if agent_id is not None:
        stmt = stmt.where(m.ChatSession.agent_id == agent_id)
    stmt = stmt.order_by(m.ChatSession.last_message_at.desc().nulls_last(), m.ChatSession.id.desc())
    return list((await db.execute(stmt)).scalars())


async def list_messages(
    db: AsyncSession, *, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> list[m.ChatMessage]:
    stmt = (
        select(m.ChatMessage)
        .where(m.ChatMessage.tenant_id == tenant_id, m.ChatMessage.session_id == session_id)
        .order_by(m.ChatMessage.id.asc())
    )
    return list((await db.execute(stmt)).scalars())


def _build_task_text(history: list[m.ChatMessage], new_message: str) -> str:
    """The run's `task` text: the whole visible conversation, plain-labelled,
    ending in the new turn. `run_agent`/`internal_agent.py` know nothing of
    "chat" -- from their side this is just one more task instruction, so any
    continuity across turns has to be spelled out here rather than relying
    on native multi-turn state that does not exist on that path."""
    lines = []
    for msg in history[-_TRANSCRIPT_TURNS:]:
        speaker = "User" if msg.role == "user" else "You (assistant)"
        lines.append(f"{speaker}: {msg.content}")
    lines.append(f"User: {new_message}")
    return "\n".join(lines)


async def send_message(
    db: AsyncSession,
    *,
    session: m.ChatSession,
    tenant_id: uuid.UUID,
    message: str,
    originating_operator: str | None,
) -> tuple[m.ChatMessage, m.AgentRun]:
    """Record the user's turn and enqueue the run that answers it.

    COMMITS `db` (via `enqueue_run`) -- callers must not issue further
    queries on it afterwards, same contract `enqueue_run` itself documents.
    """
    history = await list_messages(db, tenant_id=tenant_id, session_id=session.id)
    user_message = m.ChatMessage(
        tenant_id=tenant_id, session_id=session.id, role="user", content=message
    )
    db.add(user_message)
    await db.flush()

    context: dict[str, object] = {
        "task": _build_task_text(history, message),
        "chat_session_id": str(session.id),
    }
    if originating_operator is not None:
        context["originating_operator"] = originating_operator

    run, _published = await enqueue_run(
        db,
        tenant_id=tenant_id,
        agent_id=session.agent_id,
        context=context,
        source="chat",
        task_id=session.task_id,
    )
    return user_message, run


async def record_assistant_reply(db: AsyncSession, *, run: m.AgentRun, output: str) -> None:
    """Called from `oc8.runtime.executor` once a `source="chat"` run reaches
    a terminal state -- turns its outcome into the durable transcript
    message the Chat UI reads. A no-op (logs nothing, raises nothing) for
    any run whose context carries no `chat_session_id`, so this is safe to
    call unconditionally from the executor's generic terminal-state path."""
    session_id_raw = run.context.get("chat_session_id") if run.context else None
    if not session_id_raw:
        return
    session = await get_session(
        db, tenant_id=run.tenant_id, session_id=uuid.UUID(str(session_id_raw))
    )
    if session is None:
        return
    if session.task_id is None and run.task_id is not None:
        session.task_id = run.task_id
    rendered_components = (run.context or {}).get("rendered_components", [])
    db.add(
        m.ChatMessage(
            tenant_id=run.tenant_id,
            session_id=session.id,
            role="assistant",
            content=output or "",
            run_id=run.id,
            rendered_components=rendered_components,
        )
    )
    session.last_message_at = dt.datetime.now(tz=dt.UTC)
