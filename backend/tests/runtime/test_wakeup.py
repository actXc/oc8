from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.engine import RunResult
from oc8.constants import ACME_TENANT_ID
from oc8.runtime.executor import execute_run
from oc8.runtime.queue import RunMessage
from oc8.runtime.repository import RunRepository
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


class _RecordingQueue:
    def __init__(self) -> None:
        self.enqueued: list[uuid.UUID] = []

    async def enqueue(self, *, run_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        self.enqueued.append(run_id)


class _FnRuntime:
    def __init__(self, fn: Any) -> None:
        self._fn = fn

    async def execute(self, db: Any, **kw: Any) -> RunResult:
        return await self._fn(db, **kw)  # type: ignore[no-any-return]


def _msg(run_id: uuid.UUID, tenant: uuid.UUID) -> RunMessage:
    return RunMessage(
        run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False
    )


async def _delegated_setup(
    app_session: AppSessionFactory, tenant: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A team lead with a task, and a worker with a sub-task parented to it.
    Returns (lead agent id, sub-task id, sub-run id)."""
    async with app_session(tenant) as s:
        dept_id = uuid.uuid4()
        lead = m.Agent(tenant_id=tenant, department_id=dept_id, name="Lead", is_team_lead=True)
        worker = m.Agent(tenant_id=tenant, department_id=dept_id, name="Ada")
        s.add_all([lead, worker])
        await s.flush()

        lead_task = m.Task(
            tenant_id=tenant,
            department_id=dept_id,
            assigned_agent_id=lead.id,
            title="big job",
            state="in_progress",
            delegation_depth=0,
        )
        s.add(lead_task)
        await s.flush()

        sub_task = m.Task(
            tenant_id=tenant,
            department_id=dept_id,
            assigned_agent_id=worker.id,
            title="the sub work",
            state="in_progress",
            parent_task_id=lead_task.id,
            delegation_depth=1,
        )
        s.add(sub_task)
        await s.flush()

        sub_run = await RunRepository(s).create(
            tenant_id=tenant,
            agent_id=worker.id,
            context={
                "task": "the sub work",
                "parent_task_id": str(lead_task.id),
                "delegation_depth": 1,
            },
        )
        return lead.id, sub_task.id, sub_run.id


@pytest.mark.parametrize(
    ("status", "expected_outcome"), [("done", "completed"), ("failed", "failed")]
)
async def test_finishing_a_sub_task_wakes_the_team_lead(
    app_session: AppSessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected_outcome: str,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    lead_id, sub_task_id, sub_run_id = await _delegated_setup(app_session, tenant)
    queue = _RecordingQueue()
    monkeypatch.setattr("oc8.runtime.intake.get_run_queue", lambda: queue)

    async def runner(db: Any, **kw: Any) -> RunResult:
        return RunResult(
            task_id=sub_task_id,
            agent_id=kw["agent"].id,
            status=status,
            output="the report is attached",
            tool_calls=[],
            steps=1,
        )

    await execute_run(_msg(sub_run_id, tenant), runtime=_FnRuntime(runner))

    assert len(queue.enqueued) == 1
    async with app_session(tenant) as s:
        wake = await s.get(m.AgentRun, queue.enqueued[0])
        assert wake is not None
        assert wake.agent_id == lead_id
        assert wake.state == "queued"
        assert wake.source == "delegation"
        assert expected_outcome in wake.context["task"]
        assert "the sub work" in wake.context["task"]
        assert "the report is attached" in wake.context["task"]
        # A wake-up is a continuation, not a new hop: it carries the sub-task's
        # depth unchanged, so the chain still terminates.
        assert wake.context["delegation_depth"] == 1


async def test_finishing_an_unparented_task_wakes_nobody(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    queue = _RecordingQueue()
    monkeypatch.setattr("oc8.runtime.intake.get_run_queue", lambda: queue)

    async with app_session(tenant) as s:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="Solo")
        s.add(agent)
        await s.flush()
        task = m.Task(
            tenant_id=tenant,
            department_id=agent.department_id,
            assigned_agent_id=agent.id,
            title="just a task",
            state="in_progress",
        )
        s.add(task)
        await s.flush()
        run = await RunRepository(s).create(
            tenant_id=tenant, agent_id=agent.id, context={"task": "x"}
        )
        run_id, task_id, agent_id = run.id, task.id, agent.id

    async def runner(db: Any, **kw: Any) -> RunResult:
        return RunResult(
            task_id=task_id, agent_id=agent_id, status="done", output="ok", tool_calls=[], steps=1
        )

    await execute_run(_msg(run_id, tenant), runtime=_FnRuntime(runner))

    assert queue.enqueued == []


async def test_a_sub_task_that_raises_still_wakes_the_team_lead(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real failure mode: run_agent RAISES (it never returns status='failed').
    The executor's exception path must still wake the lead with the failure text
    so §7 reassignment can happen -- this is the path the stub-returns-'failed'
    test could not cover."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    lead_id, _sub_task_id, sub_run_id = await _delegated_setup(app_session, tenant)
    queue = _RecordingQueue()
    monkeypatch.setattr("oc8.runtime.intake.get_run_queue", lambda: queue)

    async def boom(db: Any, **kw: Any) -> RunResult:
        raise RuntimeError("model router exhausted")

    await execute_run(_msg(sub_run_id, tenant), runtime=_FnRuntime(boom))

    # The sub-run itself is FAILED...
    async with app_session(tenant) as s:
        sub = await s.get(m.AgentRun, sub_run_id)
        assert sub is not None
        assert sub.state == "failed"
        # ...and exactly one wake was published for the lead, carrying the failure.
    assert len(queue.enqueued) == 1
    async with app_session(tenant) as s:
        wake = await s.get(m.AgentRun, queue.enqueued[0])
        assert wake is not None
        assert wake.agent_id == lead_id
        assert wake.state == "queued"
        assert wake.source == "delegation"
        assert "failed" in wake.context["task"]
        assert "model router exhausted" in wake.context["task"]
        assert wake.context["delegation_depth"] == 1


async def test_a_team_leads_own_wake_up_task_does_not_wake_it_again(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same-agent guard. A wake-up task is assigned to the team lead AND
    parented to the lead's own earlier task, which is also assigned to it.
    Without the guard that would wake it forever, and delegation_depth (which a
    wake-up carries unchanged) would never rise to stop it."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    queue = _RecordingQueue()
    monkeypatch.setattr("oc8.runtime.intake.get_run_queue", lambda: queue)

    async with app_session(tenant) as s:
        dept_id = uuid.uuid4()
        lead = m.Agent(tenant_id=tenant, department_id=dept_id, name="Lead", is_team_lead=True)
        s.add(lead)
        await s.flush()
        original = m.Task(
            tenant_id=tenant,
            department_id=dept_id,
            assigned_agent_id=lead.id,
            title="big job",
            state="in_progress",
        )
        s.add(original)
        await s.flush()
        wake_task = m.Task(
            tenant_id=tenant,
            department_id=dept_id,
            assigned_agent_id=lead.id,
            title="follow-up",
            state="in_progress",
            parent_task_id=original.id,
            delegation_depth=1,
        )
        s.add(wake_task)
        await s.flush()
        run = await RunRepository(s).create(
            tenant_id=tenant,
            agent_id=lead.id,
            context={
                "task": "follow-up",
                "parent_task_id": str(original.id),
                "delegation_depth": 1,
            },
        )
        run_id, wake_task_id, lead_id = run.id, wake_task.id, lead.id

    async def runner(db: Any, **kw: Any) -> RunResult:
        return RunResult(
            task_id=wake_task_id,
            agent_id=lead_id,
            status="done",
            output="wrapped up",
            tool_calls=[],
            steps=1,
        )

    await execute_run(_msg(run_id, tenant), runtime=_FnRuntime(runner))

    assert queue.enqueued == []
