from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.engine import run_agent
from oc8.constants import ACME_TENANT_ID
from oc8.modelrouter import CompletionResult, ToolCall, Usage, chunk_from_result
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


class _CountingRouter:
    """Always asks for a tool call, so run_agent would loop forever if not
    stopped -- lets us prove cancellation halts it before the next model call.
    Counts how many times complete() was invoked."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, req: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            text="",
            tool_calls=[ToolCall(id=f"c{self.calls}", name="noop", arguments={})],
            usage=Usage(tokens_in=1, tokens_out=1),
            stop_reason="tool_use",
            provider="fake",
            model="fake",
        )

    async def stream(self, req: Any) -> Any:
        yield chunk_from_result(await self.complete(req))


async def _make_agent(db: Any, tenant: uuid.UUID) -> m.Agent:
    dept = m.Department(tenant_id=tenant, name="Ops", frame={})
    db.add(dept)
    await db.flush()
    agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Rep")
    db.add(agent)
    await db.flush()
    return agent


async def test_cancel_check_stops_before_the_next_model_call(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    router = _CountingRouter()
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: router)

    # Cancel becomes true after the first model call (fires on the 2nd step check).
    state = {"step": 0}

    async def cancel_check() -> bool:
        state["step"] += 1
        return state["step"] > 1  # False on step 1's check, True on step 2's

    async with app_session(tenant) as db:
        agent = await _make_agent(db, tenant)
        result = await run_agent(
            db, agent=agent, task_text="loop", tenant_id=tenant, cancel_check=cancel_check
        )

    assert result.status == "interrupted"
    # Exactly one model call happened: step 1 ran (check False), step 2's check
    # fired True and returned BEFORE the 2nd complete().
    assert router.calls == 1


async def test_cancel_check_none_runs_normally(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))

    class _DoneRouter:
        async def complete(self, req: Any) -> CompletionResult:
            return CompletionResult(
                text="done",
                tool_calls=[],
                usage=Usage(tokens_in=1, tokens_out=1),
                stop_reason="stop",
                provider="fake",
                model="fake",
            )

        async def stream(self, req: Any) -> Any:
            yield chunk_from_result(await self.complete(req))

    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: _DoneRouter())

    async with app_session(tenant) as db:
        agent = await _make_agent(db, tenant)
        # Default cancel_check=None: byte-identical to today.
        result = await run_agent(db, agent=agent, task_text="work", tenant_id=tenant)

    assert result.status == "done"
