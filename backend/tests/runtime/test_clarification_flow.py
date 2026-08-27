from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.modelrouter import CompletionResult, ToolCall, Usage, chunk_from_result
from oc8.models.run import Clarification
from oc8.runtime.clarification import resolve_clarification
from oc8.runtime.executor import execute_run
from oc8.runtime.queue import RunMessage
from oc8.runtime.repository import RunRepository
from oc8.runtime.states import RunState
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


class _AsksThenWouldStop:
    """First model call invokes ask_user; the run must suspend before a 2nd call.

    Reused from tests/agents/test_ask_user.py (Task 1) -- exercises the same
    engine path here, but driven through the executor rather than run_agent
    directly, so this test proves the executor's handling of the resulting
    waiting_for_input RunResult, not just the engine's.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, req: Any) -> CompletionResult:
        self.calls += 1
        if self.calls == 1:
            return CompletionResult(
                text="",
                tool_calls=[ToolCall(id="c1", name="ask_user",
                                     arguments={"question": "Which bank account?"})],
                usage=Usage(1, 1), stop_reason="tool_use", provider="ollama", model="m",
            )
        return CompletionResult(
            text="should-not-be-reached", tool_calls=[], usage=Usage(1, 1),
            stop_reason="stop", provider="ollama", model="m",
        )

    async def stream(self, req: Any) -> Any:
        yield chunk_from_result(await self.complete(req))


async def test_waiting_run_has_open_clarification_and_pending_question(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # After execute_run on a run whose agent asks, the run is WAITING_FOR_INPUT,
    # there is one open Clarification with the question, and
    # run.context["pending_question"] == the question.
    router = _AsksThenWouldStop()
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: router)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="Rep")
        db.add(agent)
        await db.flush()
        repo = RunRepository(db)
        run = await repo.create(
            tenant_id=tenant, agent_id=agent.id, context={"task": "pay the invoice"}
        )
        run_id = run.id

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False),
    )

    async with app_session(tenant) as db:
        fetched = await db.get(m.AgentRun, run_id)
        assert fetched is not None
        assert fetched.state == RunState.WAITING_FOR_INPUT.value
        assert fetched.context["pending_question"] == "Which bank account?"

        clars = (
            (
                await db.execute(
                    select(Clarification).where(Clarification.run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(clars) == 1
        assert clars[0].status == "open"
        assert clars[0].question == "Which bank account?"

    assert router.calls == 1  # suspended before a second model call


async def test_resolve_clears_pending_question(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="A")
        db.add(agent)
        await db.flush()
        run = m.AgentRun(
            tenant_id=tenant, agent_id=agent.id, state="waiting_for_input",
            context={"pending_question": "which one?", "task": "t"},
        )
        db.add(run)
        # Flush first so run.id (a client-side default applied at flush time,
        # not on construction) is populated before Clarification references it
        # -- the brief's original ordering left run.id None, tripping the
        # clarification.run_id NOT NULL constraint.
        await db.flush()
        db.add(Clarification(tenant_id=tenant, run_id=run.id, agent_id=agent.id,
                             question="which one?", status="open"))
        await db.flush()
        await resolve_clarification(db, run=run, answer="the blue one")
        assert "pending_question" not in run.context
        assert run.state == "queued"
        assert run.context["clarifications"][-1] == {
            "question": "which one?", "answer": "the blue one"
        }
