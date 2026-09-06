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
            frame={"tools": {"coding": {"enabled": True, "read": True, "write": True,
                                         "send": False, "approval_eur": None}}},
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


async def test_run_agent_offers_list_pending_approvals_to_the_assistant_with_approval_view(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: a real chat-driven Assistant run offers the status tool
    once the human behind it holds APPROVAL_VIEW -- proving preamble.py's
    copilot_permissions actually reaches offered_tools through run_agent,
    not just through build_run_preamble in isolation (Task 8's own test)."""
    from oc8.authz.permissions import ORG_ADMIN

    captured: list[list[str]] = []

    class _CapturingRouter:
        async def complete(self, req: Any) -> CompletionResult:
            captured.append([t.name for t in req.tools])
            return CompletionResult(
                text="done", tool_calls=[], usage=Usage(tokens_in=1, tokens_out=1),
                stop_reason="stop", provider="fake", model="fake",
            )

        async def stream(self, req: Any) -> Any:
            yield chunk_from_result(await self.complete(req))

    tenant = uuid.uuid4()
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: _CapturingRouter())

    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="oc8 Assistant", frame={})
        db.add(dept)
        await db.flush()
        assistant = m.Agent(
            tenant_id=tenant, department_id=dept.id, name="Assistant", status="running",
            definition={}, presentation={}, is_team_lead=True, is_tenant_assistant=True,
        )
        db.add(assistant)
        await db.flush()

        task = m.Task(
            tenant_id=tenant, department_id=dept.id, assigned_agent_id=assistant.id,
            title="Chat", state="in_progress",
        )
        db.add(task)
        await db.flush()

        run = m.AgentRun(
            tenant_id=tenant, agent_id=assistant.id, task_id=task.id, state="queued"
        )
        db.add(run)
        await db.flush()

        role = m.Role(tenant_id=tenant, name=ORG_ADMIN, builtin=True, kind="human")
        db.add(role)
        await db.flush()
        member = m.OrgMember(
            tenant_id=tenant, subject=f"anna-{uuid.uuid4()}",
            subject_uuid=uuid.uuid4(), role_id=role.id,
        )
        db.add(member)
        await db.flush()
        db.add(
            m.ChatSession(
                tenant_id=tenant, agent_id=assistant.id, member_id=member.id, task_id=task.id
            )
        )
        await db.flush()

        await run_agent(db, agent=assistant, task_text="Hallo", tenant_id=tenant, run_id=run.id)

    assert captured, "the scripted router must have been called at least once"
    assert "list_pending_approvals" in captured[0]
