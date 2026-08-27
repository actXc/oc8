# backend/tests/coding/test_engine_budget.py
from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.agent.engine import run_agent
from oc8.constants import ACME_TENANT_ID
from oc8.metering.budget import set_budget
from oc8.metering.usage import record_usage
from oc8.modelrouter import CompletionResult, Usage, chunk_from_result
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


class _CapturingCompletionRouter:
    def __init__(self) -> None:
        self.call_count = 0

    async def complete(self, req: Any) -> CompletionResult:
        self.call_count += 1
        return CompletionResult(
            text="done", tool_calls=[], usage=Usage(tokens_in=3, tokens_out=3),
            stop_reason="stop", provider="fake", model="fake",
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


async def test_hard_limit_exceeded_pauses_agent_without_calling_router(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent = await _make_agent(db, tenant)
        await set_budget(
            db, tenant_id=tenant, department_id=agent.department_id,
            soft_limit_tokens=None, hard_limit_tokens=100,
        )
        await record_usage(
            db, tenant_id=tenant, request_id=uuid.uuid4(), model="m", provider="p",
            tokens_in=80, tokens_out=30, department_id=agent.department_id,
        )
        completion_router = _CapturingCompletionRouter()
        monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: completion_router)

        result = await run_agent(db, agent=agent, task_text="do work", tenant_id=tenant)
        assert result.status == "budget_exceeded"
        assert agent.status == "paused"
        assert completion_router.call_count == 0


async def test_soft_limit_exceeded_completes_with_warning_activity(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent = await _make_agent(db, tenant)
        await set_budget(
            db, tenant_id=tenant, department_id=agent.department_id,
            soft_limit_tokens=50, hard_limit_tokens=1_000_000,
        )
        await record_usage(
            db, tenant_id=tenant, request_id=uuid.uuid4(), model="m", provider="p",
            tokens_in=60, tokens_out=0, department_id=agent.department_id,
        )
        completion_router = _CapturingCompletionRouter()
        monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: completion_router)

        result = await run_agent(db, agent=agent, task_text="do work", tenant_id=tenant)
        assert result.status == "done"
        assert completion_router.call_count == 1

        events = (
            await db.execute(
                select(m.ActivityEvent).where(
                    m.ActivityEvent.agent_id == agent.id, m.ActivityEvent.status == "warning"
                )
            )
        ).scalars().all()
        assert any("budget" in (e.message or "").lower() for e in events)


async def test_no_budget_configured_completes_normally(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent = await _make_agent(db, tenant)
        # No Budget row for this department or tenant-wide.
        completion_router = _CapturingCompletionRouter()
        monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: completion_router)

        result = await run_agent(db, agent=agent, task_text="do work", tenant_id=tenant)
        assert result.status == "done"
        assert completion_router.call_count == 1
