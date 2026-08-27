from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.engine import run_agent
from oc8.modelrouter import CompletionResult, ToolCall, Usage, chunk_from_result
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


class _AsksThenWouldStop:
    """First model call invokes ask_user; the run must suspend before a 2nd call."""

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


async def test_ask_user_suspends_with_the_question(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = _AsksThenWouldStop()
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: router)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Ops", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Rep")
        db.add(agent)
        await db.flush()
        result = await run_agent(db, agent=agent, task_text="pay the invoice", tenant_id=tenant)
    assert result.status == "waiting_for_input"
    assert result.output == "Which bank account?"
    assert router.calls == 1  # suspended before a second model call


async def test_ask_user_with_empty_question_does_not_suspend(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _EmptyAsk:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, req: Any) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                return CompletionResult(
                    text="", tool_calls=[ToolCall(id="c1", name="ask_user",
                                                  arguments={"question": "  "})],
                    usage=Usage(1, 1), stop_reason="tool_use", provider="ollama", model="m",
                )
            return CompletionResult(text="done", tool_calls=[], usage=Usage(1, 1),
                                    stop_reason="stop", provider="ollama", model="m")

        async def stream(self, req: Any) -> Any:
            yield chunk_from_result(await self.complete(req))

    router = _EmptyAsk()
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: router)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Ops", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Rep")
        db.add(agent)
        await db.flush()
        result = await run_agent(db, agent=agent, task_text="do it", tenant_id=tenant)
    assert result.status != "waiting_for_input"  # empty question -> error to model, continues


class _StopsImmediately:
    """A model that just answers -- enough to open (or reuse) the run's task."""

    async def complete(self, req: Any) -> CompletionResult:
        return CompletionResult(
            text="fertig", tool_calls=[], usage=Usage(1, 1),
            stop_reason="stop", provider="ollama", model="m",
        )

    async def stream(self, req: Any) -> Any:
        yield chunk_from_result(await self.complete(req))


async def test_a_resumed_run_continues_its_task_instead_of_opening_a_second(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The in-process engine, given the run it is executing, continues that run's
    existing task on a resume leg. Opening a second one would leave the
    suspended task stranded in waiting_for_approval forever."""
    from sqlalchemy import func, select

    from oc8.runtime.states import RunState

    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: _StopsImmediately())
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Ops", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Rep")
        db.add(agent)
        await db.flush()
        # A run suspended earlier: it already carries the task its first leg opened.
        suspended = m.Task(
            tenant_id=tenant, department_id=dept.id, assigned_agent_id=agent.id,
            title="Erstelle ein Angebot", state="waiting_for_approval",
        )
        db.add(suspended)
        await db.flush()
        run = m.AgentRun(
            tenant_id=tenant, agent_id=agent.id, task_id=suspended.id,
            state=RunState.RUNNING.value, context={"task": "Erstelle ein Angebot"},
        )
        db.add(run)
        await db.flush()

        result = await run_agent(
            db, agent=agent, task_text="Fortsetzung nach Freigabe. FREIGEGEBEN — create_record",
            tenant_id=tenant, run_id=run.id,
        )
        assert result.task_id == suspended.id
        total = (
            await db.execute(select(func.count()).select_from(m.Task).where(
                m.Task.tenant_id == tenant
            ))
        ).scalar_one()
        assert total == 1, "a second task would be an orphan on the board"
        reloaded = await db.get(m.Task, suspended.id)
        assert reloaded is not None
        assert reloaded.state == "done"
        assert reloaded.title == "Erstelle ein Angebot"
