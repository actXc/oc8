# backend/tests/agents/test_length_truncation.py
"""A reasoning-capable model can spend its whole completion budget on hidden
reasoning tokens and hit max_tokens before writing anything visible.
`stop_reason == "length"` with no tool call and no text is that, not a real
stop -- and treating it as one silently reported "done" with nothing done.

Caught live: Lennart (z-ai/glm-5.3-flash via OpenRouter) read a ticket, then
one step came back with empty text and no tool calls; tokens_out for that
call was exactly the configured max_tokens (1536). The run was reported
"done" with a blank summary and the ticket untouched.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.engine import run_agent
from oc8.modelrouter import CompletionResult, ToolCall, Usage, chunk_from_result
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


class _TruncatedThenAnswers:
    """First call: truncated by max_tokens with nothing produced. Second
    call (the retry): a real answer. Records each call's params.max_tokens
    so a test can assert the retry actually asked for more room."""

    def __init__(self) -> None:
        self.calls = 0
        self.seen_max_tokens: list[int] = []

    async def complete(self, req: Any) -> CompletionResult:
        self.calls += 1
        self.seen_max_tokens.append(req.params.max_tokens)
        if self.calls == 1:
            return CompletionResult(
                text="", tool_calls=[], usage=Usage(6832, 1536),
                stop_reason="length", provider="openrouter", model="z-ai/glm-5.3-flash",
            )
        return CompletionResult(
            text="Ticket resolved, approval code BIOS-7743-QUARTZ noted.", tool_calls=[],
            usage=Usage(7000, 40), stop_reason="stop",
            provider="openrouter", model="z-ai/glm-5.3-flash",
        )

    async def stream(self, req: Any) -> Any:
        yield chunk_from_result(await self.complete(req))


class _AlwaysTruncated:
    """Every call is truncated with nothing produced -- the retry must not
    turn into an unbounded loop, and the run must end as failed, not done."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, req: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            text="", tool_calls=[], usage=Usage(8000, 1536),
            stop_reason="length", provider="openrouter", model="z-ai/glm-5.3-flash",
        )

    async def stream(self, req: Any) -> Any:
        yield chunk_from_result(await self.complete(req))


class _TruncatedButWithAToolCall:
    """Truncated (stop_reason == "length") but a tool call still came through
    -- this is NOT the empty-truncation case and must proceed normally,
    never retried."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, req: Any) -> CompletionResult:
        self.calls += 1
        if self.calls == 1:
            return CompletionResult(
                text="", tool_calls=[ToolCall(id="c1", name="memory_write",
                                              arguments={"tier": "agent", "content": "noted"})],
                usage=Usage(100, 1536), stop_reason="length",
                provider="openrouter", model="z-ai/glm-5.3-flash",
            )
        return CompletionResult(
            text="done", tool_calls=[], usage=Usage(120, 10), stop_reason="stop",
            provider="openrouter", model="z-ai/glm-5.3-flash",
        )

    async def stream(self, req: Any) -> Any:
        yield chunk_from_result(await self.complete(req))


async def test_empty_length_truncation_is_retried_with_a_bigger_budget(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = _TruncatedThenAnswers()
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: router)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="IT-Support", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Lennart")
        db.add(agent)
        await db.flush()
        result = await run_agent(
            db, agent=agent, task_text="Handle the ticket", tenant_id=tenant
        )
    assert result.status == "done"
    assert "BIOS-7743-QUARTZ" in result.output
    assert router.calls == 2
    assert router.seen_max_tokens[1] > router.seen_max_tokens[0]


async def test_persistent_empty_truncation_reports_failed_not_done(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = _AlwaysTruncated()
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: router)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="IT-Support", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Lennart")
        db.add(agent)
        await db.flush()
        result = await run_agent(
            db, agent=agent, task_text="Handle the ticket", tenant_id=tenant
        )
    assert result.status == "failed"
    # Exactly one retry, never an unbounded loop.
    assert router.calls == 2


async def test_a_truncated_call_that_still_produced_a_tool_call_is_not_retried(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = _TruncatedButWithAToolCall()
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: router)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="IT-Support", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Lennart")
        db.add(agent)
        await db.flush()
        result = await run_agent(
            db, agent=agent, task_text="Handle the ticket", tenant_id=tenant
        )
    assert result.status == "done"
    # One completion, one tool call, one closing completion -- no retry inserted.
    assert router.calls == 2
