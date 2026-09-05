"""Tool-call timing instrumentation (Agent KPIs, Task 2).

Every tool call that actually goes through the shared `tool_trace.append` at
engine.py:1086 must carry `startedAt` (ISO 8601) and `durationMs` (int) --
Task 3's aggregation module needs those to compute per-tool averages. The
OTHER two `tool_trace.append` sites (pre_hook.blocked at ~879-888 and
require_approval at ~967-974) must NOT gain these keys: the tool was never
dispatched at those points, so there is no real duration to record.

The same rule holds INSIDE the shared append site: the DENY / no-server branch
and the already-delivered refusal branch both fall through to it without ever
reaching `server.call`, so they omit the keys too (`_tool_call_dispatched`).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.engine import run_agent
from oc8.authz.pdp import Decision, Effect
from oc8.capas.claude_hooks.runner import DispatchResult
from oc8.modelrouter import CompletionResult, NeutralTool, ToolCall, Usage, chunk_from_result
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


class _FakeToolset:
    """Minimal `oc8.coding.tools.Toolset` -- no real sandbox needed, engine.py
    only ever calls `.tools` and `.call` on it."""

    def __init__(self, call: Callable[[str, dict[str, Any]], Awaitable[str]]) -> None:
        self.tools = [NeutralTool(name="fs_write", description="write a file", parameters={})]
        self._call = call

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        return await self._call(name, arguments)


class _ScriptedRouter:
    """Emits one fs_write tool call, then a final text with no tool calls."""

    def __init__(self) -> None:
        self._calls = 0

    async def complete(self, req: Any) -> CompletionResult:
        self._calls += 1
        if self._calls == 1:
            return CompletionResult(
                text="",
                tool_calls=[ToolCall(id="c1", name="fs_write", arguments={"path": "/tmp/out.txt"})],
                usage=Usage(tokens_in=1, tokens_out=1),
                stop_reason="tool_use",
                provider="fake",
                model="fake",
            )
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


async def _coding_agent(db: Any, tenant: uuid.UUID) -> m.Agent:
    dept = m.Department(
        tenant_id=tenant,
        name="Eng",
        frame={
            "tools": {
                "coding": {
                    "enabled": True,
                    "read": True,
                    "modify": True,
                    "modify": False,
                    "approval_eur": None,
                }
            }
        },
    )
    db.add(dept)
    await db.flush()
    agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Coder")
    db.add(agent)
    await db.flush()
    return agent


async def test_a_successful_tool_call_records_started_at_and_duration(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: _ScriptedRouter())

    async def _ok(name: str, arguments: dict[str, Any]) -> str:
        return "wrote it"

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = await _coding_agent(db, tenant)
        result = await run_agent(
            db,
            agent=agent,
            task_text="write the file",
            tenant_id=tenant,
            toolset=_FakeToolset(_ok),
        )

    entry = result.tool_calls[-1]
    assert entry["result"] == "wrote it"
    assert "startedAt" in entry
    assert isinstance(entry["durationMs"], int)
    assert entry["durationMs"] >= 0


async def test_an_errored_tool_call_still_records_timing(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: _ScriptedRouter())

    async def _boom(name: str, arguments: dict[str, Any]) -> str:
        raise RuntimeError("disk full")

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = await _coding_agent(db, tenant)
        result = await run_agent(
            db,
            agent=agent,
            task_text="write the file",
            tenant_id=tenant,
            toolset=_FakeToolset(_boom),
        )

    entry = result.tool_calls[-1]
    assert entry["result"].startswith("ERROR:")
    assert "startedAt" in entry
    assert "durationMs" in entry


async def test_a_guardrail_blocked_call_has_no_duration(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pre_hook.blocked is True -- this hits the OTHER append site
    (engine.py:879-888), which this task does not touch."""
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: _ScriptedRouter())

    async def _dispatch_blocking_pre_tool_use(
        tenant_id: uuid.UUID, event: str, payload: dict[str, Any], *, tool_name: str | None = None
    ) -> DispatchResult:
        if event == "PreToolUse":
            return DispatchResult(blocked=True, reason="blocked by guardrail")
        return DispatchResult()

    monkeypatch.setattr("oc8.agent.engine.dispatch_claude_event", _dispatch_blocking_pre_tool_use)

    async def _never(name: str, arguments: dict[str, Any]) -> str:
        raise AssertionError("the tool must never actually be dispatched once blocked")

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = await _coding_agent(db, tenant)
        result = await run_agent(
            db,
            agent=agent,
            task_text="write the file",
            tenant_id=tenant,
            toolset=_FakeToolset(_never),
        )

    entry = result.tool_calls[-1]
    assert entry["result"].startswith("ERROR:")
    assert "startedAt" not in entry
    assert "durationMs" not in entry


async def test_a_denied_call_has_no_duration(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guardrail DENY is refused before `server.call` is ever reached, so the
    shared append site must omit both timing keys -- exactly as the
    `pre_hook.blocked` short-circuit above already does.

    This branch DOES fall through to the shared append (unlike the blocked one,
    which `continue`s), so it is the one that could quietly record a near-zero
    `durationMs` for a call that never ran and drag `avgToolCallDurationMs`
    toward zero on every denial.
    """
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: _ScriptedRouter())
    monkeypatch.setattr(
        "oc8.agent.engine._authorize",
        lambda *a, **k: Decision(Effect.DENY, "tool not allowed by the department frame"),
    )

    async def _never(name: str, arguments: dict[str, Any]) -> str:
        raise AssertionError("the tool must never actually be dispatched once denied")

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = await _coding_agent(db, tenant)
        result = await run_agent(
            db,
            agent=agent,
            task_text="write the file",
            tenant_id=tenant,
            toolset=_FakeToolset(_never),
        )

    entry = result.tool_calls[-1]
    assert entry["result"].startswith("ERROR:")
    assert "startedAt" not in entry
    assert "durationMs" not in entry
