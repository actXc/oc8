from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.agent.engine import MAX_DELEGATION_DEPTH, run_agent
from oc8.constants import ACME_TENANT_ID
from oc8.modelrouter import CompletionResult, ToolCall, Usage, chunk_from_result
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


class _NoToolRouter:
    """Finishes immediately: no tool calls, so run_agent takes its `done` exit."""

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


async def _make_agent(db: Any, tenant: uuid.UUID, *, is_team_lead: bool = False) -> m.Agent:
    dept = m.Department(tenant_id=tenant, name="Engineering", frame={})
    db.add(dept)
    await db.flush()
    agent = m.Agent(
        tenant_id=tenant,
        department_id=dept.id,
        name="Lead" if is_team_lead else "Worker",
        is_team_lead=is_team_lead,
    )
    db.add(agent)
    await db.flush()
    return agent


async def test_run_agent_records_parent_task_id_and_delegation_depth(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: _NoToolRouter())

    async with app_session(tenant) as db:
        agent = await _make_agent(db, tenant)
        parent_id = uuid.uuid4()
        result = await run_agent(
            db,
            agent=agent,
            task_text="sub work",
            tenant_id=tenant,
            parent_task_id=parent_id,
            delegation_depth=3,
        )
        task = await db.get(m.Task, result.task_id)
        assert task is not None
        assert task.parent_task_id == parent_id
        assert task.delegation_depth == 3


async def test_run_agent_defaults_to_an_unparented_root_task(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: _NoToolRouter())

    async with app_session(tenant) as db:
        agent = await _make_agent(db, tenant)
        result = await run_agent(db, agent=agent, task_text="root work", tenant_id=tenant)
        task = await db.get(m.Task, result.task_id)
        assert task is not None
        assert task.parent_task_id is None
        assert task.delegation_depth == 0


class _DelegateScriptedRouter:
    """Emits one delegate_task call, then finishes."""

    def __init__(self, agent_id: str, task_text: str = "do the sub work") -> None:
        self._agent_id = agent_id
        self._task_text = task_text
        self._calls = 0

    async def complete(self, req: Any) -> CompletionResult:
        self._calls += 1
        if self._calls == 1:
            return CompletionResult(
                text="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="delegate_task",
                        arguments={"agent_id": self._agent_id, "task_text": self._task_text},
                    )
                ],
                usage=Usage(tokens_in=5, tokens_out=5),
                stop_reason="tool_use",
                provider="fake",
                model="fake",
            )
        return CompletionResult(
            text="delegated",
            tool_calls=[],
            usage=Usage(tokens_in=3, tokens_out=3),
            stop_reason="stop",
            provider="fake",
            model="fake",
        )

    async def stream(self, req: Any) -> Any:
        yield chunk_from_result(await self.complete(req))


async def _add_agent_to(db: Any, lead: m.Agent, name: str) -> m.Agent:
    mate = m.Agent(tenant_id=lead.tenant_id, department_id=lead.department_id, name=name)
    db.add(mate)
    await db.flush()
    return mate


async def test_delegate_task_is_only_offered_to_a_team_lead(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    captured: list[list[str]] = []

    async def capture(req: Any) -> CompletionResult:
        captured.append([t.name for t in req.tools])
        return CompletionResult(
            text="done",
            tool_calls=[],
            usage=Usage(tokens_in=1, tokens_out=1),
            stop_reason="stop",
            provider="fake",
            model="fake",
        )

    class _Router:
        complete = staticmethod(capture)

        async def stream(self, req: Any) -> Any:
            yield chunk_from_result(await capture(req))

    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: _Router())

    async with app_session(tenant) as db:
        worker = await _make_agent(db, tenant, is_team_lead=False)
        await run_agent(db, agent=worker, task_text="work", tenant_id=tenant)
        assert "delegate_task" not in captured[0]
        assert "memory_write" in captured[0]

        lead = await _make_agent(db, tenant, is_team_lead=True)
        await run_agent(db, agent=lead, task_text="work", tenant_id=tenant)
        assert "delegate_task" in captured[1]


async def test_delegate_task_creates_a_queued_sub_run_without_publishing(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))

    async with app_session(tenant) as db:
        lead = await _make_agent(db, tenant, is_team_lead=True)
        mate = await _add_agent_to(db, lead, "Ada")
        monkeypatch.setattr(
            "oc8.agent.engine.get_model_router", lambda: _DelegateScriptedRouter(str(mate.id))
        )

        result = await run_agent(db, agent=lead, task_text="big job", tenant_id=tenant)
        assert result.status == "done"

        assert len(result.pending_runs) == 1
        sub_run = await db.get(m.AgentRun, result.pending_runs[0])
        assert sub_run is not None
        assert sub_run.agent_id == mate.id
        assert sub_run.state == "queued"
        assert sub_run.source == "delegation"
        assert sub_run.context["task"] == "do the sub work"
        assert sub_run.context["parent_task_id"] == str(result.task_id)
        assert sub_run.context["delegation_depth"] == 1


async def test_delegate_task_rejects_a_target_in_another_department(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))

    async with app_session(tenant) as db:
        lead = await _make_agent(db, tenant, is_team_lead=True)
        outsider = await _make_agent(db, tenant)  # its own department
        monkeypatch.setattr(
            "oc8.agent.engine.get_model_router", lambda: _DelegateScriptedRouter(str(outsider.id))
        )

        result = await run_agent(db, agent=lead, task_text="big job", tenant_id=tenant)
        assert result.pending_runs == []
        assert any("own department" in str(tc.get("result", "")) for tc in result.tool_calls)


async def test_delegate_task_rejects_self_delegation(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))

    async with app_session(tenant) as db:
        lead = await _make_agent(db, tenant, is_team_lead=True)
        monkeypatch.setattr(
            "oc8.agent.engine.get_model_router", lambda: _DelegateScriptedRouter(str(lead.id))
        )

        result = await run_agent(db, agent=lead, task_text="big job", tenant_id=tenant)
        assert result.pending_runs == []
        assert any("itself" in str(tc.get("result", "")) for tc in result.tool_calls)


async def test_delegate_task_rejects_an_unknown_agent_id(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))

    async with app_session(tenant) as db:
        lead = await _make_agent(db, tenant, is_team_lead=True)
        monkeypatch.setattr(
            "oc8.agent.engine.get_model_router",
            lambda: _DelegateScriptedRouter(str(uuid.uuid4())),
        )

        result = await run_agent(db, agent=lead, task_text="big job", tenant_id=tenant)
        assert result.pending_runs == []
        assert any("no such agent" in str(tc.get("result", "")) for tc in result.tool_calls)


async def test_delegate_task_is_denied_at_the_depth_limit(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))

    async with app_session(tenant) as db:
        lead = await _make_agent(db, tenant, is_team_lead=True)
        mate = await _add_agent_to(db, lead, "Ada")
        monkeypatch.setattr(
            "oc8.agent.engine.get_model_router", lambda: _DelegateScriptedRouter(str(mate.id))
        )

        result = await run_agent(
            db,
            agent=lead,
            task_text="big job",
            tenant_id=tenant,
            delegation_depth=MAX_DELEGATION_DEPTH,
        )
        assert result.pending_runs == []
        assert any("depth limit" in str(tc.get("result", "")) for tc in result.tool_calls)

        events = (
            (
                await db.execute(
                    select(m.ActivityEvent).where(m.ActivityEvent.agent_id == lead.id)
                )
            )
            .scalars()
            .all()
        )
        assert any("delegation depth limit" in e.message for e in events)


async def test_non_depth_deny_at_the_cap_does_not_emit_a_depth_activity_event(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A task already AT the depth cap that issues a delegate_task denied for a
    DIFFERENT reason (here: self-delegation, which _authorize checks before the
    depth check) must NOT mislabel the audit trail with a depth-limit event.
    The operator-facing ActivityEvent is gated on the actual deny reason, not a
    re-derived depth arithmetic that would be true for any DENY at the cap."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))

    async with app_session(tenant) as db:
        lead = await _make_agent(db, tenant, is_team_lead=True)
        # Router asks the lead to delegate to ITSELF -> DENY "cannot delegate to
        # itself", which precedes the depth check inside _authorize.
        monkeypatch.setattr(
            "oc8.agent.engine.get_model_router", lambda: _DelegateScriptedRouter(str(lead.id))
        )

        result = await run_agent(
            db,
            agent=lead,
            task_text="big job",
            tenant_id=tenant,
            delegation_depth=MAX_DELEGATION_DEPTH,
        )
        assert result.pending_runs == []
        # The model still sees the correct (self-delegation) error.
        assert any("itself" in str(tc.get("result", "")) for tc in result.tool_calls)

        events = (
            (
                await db.execute(
                    select(m.ActivityEvent).where(m.ActivityEvent.agent_id == lead.id)
                )
            )
            .scalars()
            .all()
        )
        # No depth-limit event: the DENY was for self-delegation, not depth.
        assert not any("delegation depth limit" in e.message for e in events)
