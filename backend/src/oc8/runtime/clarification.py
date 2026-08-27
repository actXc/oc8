"""Human-loop: suspend a run on an open question; resume when answered."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8.models.run import AgentRun, Clarification
from oc8.runtime.repository import RunRepository
from oc8.runtime.states import RunState


async def request_clarification(db: AsyncSession, *, run: AgentRun, question: str) -> Clarification:
    clar = Clarification(
        tenant_id=run.tenant_id,
        run_id=run.id,
        agent_id=run.agent_id,
        question=question,
        status="open",
    )
    db.add(clar)
    await RunRepository(db).transition(run, RunState.WAITING_FOR_INPUT)
    await db.flush()
    return clar


async def resolve_clarification(db: AsyncSession, *, run: AgentRun, answer: str) -> None:
    """Answer the run's open clarification(s), fold Q&A into `run.context`, and
    re-queue the run so the worker resumes it with the answer visible."""
    rows = (
        (
            await db.execute(
                select(Clarification).where(
                    Clarification.run_id == run.id, Clarification.status == "open"
                )
            )
        )
        .scalars()
        .all()
    )
    entries = list(run.context.get("clarifications", []))
    for clar in rows:
        clar.answer = answer
        clar.status = "answered"
        entries.append({"question": clar.question, "answer": answer})
    new_ctx = {**run.context, "clarifications": entries}
    new_ctx.pop("pending_question", None)
    run.context = new_ctx
    await RunRepository(db).transition(run, RunState.QUEUED)
    await db.flush()
