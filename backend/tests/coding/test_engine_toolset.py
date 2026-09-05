from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.engine import run_agent
from oc8.coding import coding_session
from oc8.constants import ACME_TENANT_ID
from oc8.modelrouter import CompletionResult, ToolCall, Usage, chunk_from_result
from oc8.sandbox import SandboxSpec
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


class _ScriptedRouter:
    """Emits one fs_write tool call, then a final text with no tool calls."""

    def __init__(self) -> None:
        self._calls = 0

    async def complete(self, req: Any) -> CompletionResult:
        self._calls += 1
        if self._calls == 1:
            return CompletionResult(
                text="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="fs_write",
                        arguments={"path": "/workspace/out.txt", "content": "generated"},
                    )
                ],
                usage=Usage(tokens_in=5, tokens_out=5),
                stop_reason="tool_use",
                provider="fake",
                model="fake",
            )
        return CompletionResult(
            text="done writing the file",
            tool_calls=[],
            usage=Usage(tokens_in=3, tokens_out=3),
            stop_reason="stop",
            provider="fake",
            model="fake",
        )

    async def stream(self, req: Any) -> Any:
        yield chunk_from_result(await self.complete(req))


async def test_run_agent_codes_in_sandbox(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: _ScriptedRouter())

    async with app_session(tenant) as db:
        dept = m.Department(
            tenant_id=tenant,
            name="Eng",
            frame={"tools": {"coding": {"enabled": True, "read": True, "modify": True, "approval_eur": None}}},
        )
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Coder")
        db.add(agent)
        await db.flush()

        async with coding_session(SandboxSpec(image="alpine:latest")) as ts:
            result = await run_agent(
                db,
                agent=agent,
                task_text="write the file",
                tenant_id=tenant,
                toolset=ts,
            )
            assert result.status == "done"
            # The tool call actually wrote the file inside the sandbox.
            assert await ts.call("fs_read", {"path": "/workspace/out.txt"}) == "generated"
