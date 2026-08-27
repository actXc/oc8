"""A tool_send approval whose run can no longer be resumed.

The endpoint used to record such a decision, answer 200 with a cheerful
"approved" DTO, and leave the task in waiting_for_approval forever -- with
nothing in the response, the task or the feed to say the held action never ran.
That is how a board fills up with tasks that look like they are waiting on a
human who already decided.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from oc8.runtime.states import RunState
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _token(tenant: uuid.UUID) -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")


async def _agent_task_approval(
    db: AsyncSession, tenant: uuid.UUID, *, with_run: bool
) -> tuple[uuid.UUID, uuid.UUID]:
    dept = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
    db.add(dept)
    await db.flush()
    agent = m.Agent(
        tenant_id=tenant, department_id=dept.id, name="Nora", status="running",
        narrowing={}, definition={}, presentation={},
    )
    db.add(agent)
    await db.flush()
    task = m.Task(
        tenant_id=tenant, department_id=dept.id, assigned_agent_id=agent.id,
        title="Erstelle ein Angebot", state="waiting_for_approval",
    )
    db.add(task)
    await db.flush()
    if with_run:
        db.add(
            m.AgentRun(
                tenant_id=tenant, agent_id=agent.id, task_id=task.id,
                state=RunState.WAITING_FOR_APPROVAL.value,
                context={"task": "Erstelle ein Angebot"},
            )
        )
    ar = m.ApprovalRequest(
        tenant_id=tenant, agent_id=agent.id, task_id=task.id,
        action_type="tool_send", status="pending",
        title="Nora wants to call create_record",
        detail="value €3900 meets threshold €3000",
        payload={"tool": "create_record", "arguments": {"model": "sale.order"}},
    )
    db.add(ar)
    await db.flush()
    return ar.id, task.id


async def _decide(
    tenant: uuid.UUID, approval_id: uuid.UUID, decision: str
) -> tuple[int, dict[str, Any]]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                f"/api/v1/approvals/{approval_id}/decision",
                json={"decision": decision},
                headers={"Authorization": f"Bearer {_token(tenant)}"},
            )
            return r.status_code, (r.json() if r.content else {})


async def test_approving_with_no_resumable_run_does_not_strand_the_task(
    app_session: AppSessionFactory,
) -> None:
    """No run points at this task (the pre-fix isolated runtime left approvals
    like this). The decision is still recorded -- an operator did decide, and the
    audit must show it -- but the task must reach a terminal state instead of
    waiting on a human forever."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        approval_id, task_id = await _agent_task_approval(db, tenant, with_run=False)

    code, body = await _decide(tenant, approval_id, "approve")
    assert code == 200, body
    assert body["status"] == "approved", "the operator's decision is still recorded"
    # And the operator is TOLD nothing ran, rather than being shown a bare success.
    assert body["resumed"] is False

    async with app_session(tenant) as db:
        task = await db.get(m.Task, task_id)
        assert task is not None
        assert task.state != "waiting_for_approval", "the task must not stay stranded"
        assert task.state == "failed"


async def test_the_feed_shows_that_the_held_action_never_ran(
    app_session: AppSessionFactory,
) -> None:
    """An operator who approved something is entitled to learn it did not happen."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        approval_id, _task_id = await _agent_task_approval(db, tenant, with_run=False)

    await _decide(tenant, approval_id, "approve")

    async with app_session(tenant) as db:
        events = (
            await db.execute(
                m.ActivityEvent.__table__.select().where(
                    m.ActivityEvent.tenant_id == tenant,
                    m.ActivityEvent.status == "warning",
                )
            )
        ).fetchall()
        assert len(events) == 1
        assert "could not be resumed" in events[0].message


async def test_a_resumable_run_still_resumes_and_reports_it(
    app_session: AppSessionFactory, redis_url: str
) -> None:
    """The normal path must be untouched: a waiting run is re-queued, the task is
    left alone (the run owns its lifecycle from here), and resumed is true.

    `redis_url` is requested because the re-queue is a real enqueue; without it
    the test depends on an ambient redis and fails as if the endpoint were broken.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        approval_id, task_id = await _agent_task_approval(db, tenant, with_run=True)

    code, body = await _decide(tenant, approval_id, "approve")
    assert code == 200, body
    assert body["resumed"] is True

    async with app_session(tenant) as db:
        run = (
            await db.execute(
                m.AgentRun.__table__.select().where(m.AgentRun.task_id == task_id)
            )
        ).first()
        assert run is not None
        assert run.state == RunState.QUEUED.value, "the run is re-queued for the worker"
        task = await db.get(m.Task, task_id)
        assert task is not None
        # NOT terminalised -- the resumed run decides the outcome.
        assert task.state == "waiting_for_approval"


async def test_rejecting_an_unresumable_run_also_terminalises_the_task(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        approval_id, task_id = await _agent_task_approval(db, tenant, with_run=False)

    code, body = await _decide(tenant, approval_id, "reject")
    assert code == 200, body
    assert body["status"] == "rejected"
    assert body["resumed"] is False

    async with app_session(tenant) as db:
        task = await db.get(m.Task, task_id)
        assert task is not None
        assert task.state == "failed"


async def test_a_decision_is_recorded_even_when_the_run_cannot_be_requeued(
    app_session: AppSessionFactory,
) -> None:
    """A long-lived runtime driving tools through the MCP gateway stays RUNNING --
    nothing exits, so nothing transitions the run to waiting_for_approval. The
    decision must still be written onto the run, or the harness's retry raises a
    SECOND approval and the operator can approve forever without the agent ever
    proceeding. Found live against the tool gateway, not in a test."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        approval_id, task_id = await _agent_task_approval(db, tenant, with_run=True)
        # The gateway parks via context, but the run itself is still RUNNING.
        run = (
            await db.execute(
                m.AgentRun.__table__.select().where(m.AgentRun.task_id == task_id)
            )
        ).first()
        assert run is not None
        live = await db.get(m.AgentRun, run.id)
        assert live is not None
        live.state = RunState.RUNNING.value
        live.context = {**live.context, "isolated_result": {"status": "waiting_for_approval"}}
        await db.flush()

    code, body = await _decide(tenant, approval_id, "approve")
    assert code == 200, body
    # Nothing was re-queued -- the harness is alive and will retry itself.
    assert body["resumed"] is False

    async with app_session(tenant) as db:
        live2 = (
            await db.execute(
                m.AgentRun.__table__.select().where(m.AgentRun.task_id == task_id)
            )
        ).first()
        assert live2 is not None
        entries = live2.context.get("resolved_tool_approvals") or []
        assert entries, "the operator's decision must reach the run"
        assert entries[0]["decision"] == "approve"
        assert entries[0]["tool"] == "create_record"

    async with app_session(tenant) as db:
        task = await db.get(m.Task, task_id)
        assert task is not None
        # NOT terminalised: a live run will honour the decision on its next attempt.
        assert task.state == "waiting_for_approval"
