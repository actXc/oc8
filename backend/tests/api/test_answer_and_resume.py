from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.engine import RunResult
from oc8.constants import ACME_TENANT_ID
from oc8.runtime.clarification import request_clarification
from oc8.runtime.executor import execute_run
from oc8.runtime.queue import RunMessage
from oc8.runtime.repository import RunRepository
from oc8.runtime.states import RunState
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


class _FnRuntime:
    """Wraps a bare async(db, **kw) -> RunResult callable as a RuntimeAdapter,
    for tests that inject a scripted result directly (bypassing resolve_runtime).
    Mirrors tests/runtime/test_executor.py's helper of the same name."""

    def __init__(self, fn: Callable[..., Awaitable[RunResult]]) -> None:
        self._fn = fn

    async def execute(self, db: Any, **kw: Any) -> RunResult:
        return await self._fn(db, **kw)


async def test_answer_requeues_and_resume_sees_answer(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="A")
        db.add(agent)
        await db.flush()
        run = await RunRepository(db).create(
            tenant_id=tenant, agent_id=agent.id, context={"task": "build feature"}
        )
        await RunRepository(db).transition(run, RunState.RUNNING)
        await request_clarification(db, run=run, question="Which version?")
        run_id = run.id

    # answer it directly via resolve_clarification (endpoint wraps this)
    from oc8.runtime.clarification import resolve_clarification

    async with app_session(tenant) as db:
        loaded = await db.get(m.AgentRun, run_id)
        assert loaded is not None
        await resolve_clarification(db, run=loaded, answer="Odoo 17")
        assert loaded.state == RunState.QUEUED.value

    seen_task: dict[str, str] = {}

    async def fake_runner(db: Any, **kw: Any) -> RunResult:
        seen_task["t"] = kw["task_text"]
        return RunResult(
            task_id=uuid.uuid4(),
            agent_id=kw["agent"].id,
            status="done",
            output="ok",
            tool_calls=[],
            steps=1,
        )

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False),
        runtime=_FnRuntime(fake_runner),
    )
    assert "Odoo 17" in seen_task["t"] and "build feature" in seen_task["t"]
