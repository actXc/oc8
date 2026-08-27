"""Migration 0044 must close exactly what the runtime rule would close.

The repair and the rule are written twice -- once in SQL, once in Python -- so
they can drift, and a drift here is invisible: the migration runs once, on a
database nobody is watching, and either leaves stuck rows behind or closes tasks
that were still being worked. This runs the migration's own statement against
rows built for each branch of the predicate.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

# The statement from migrations/versions/0044_close_tasks_behind_finished_runs.py,
# scoped to one tenant so it cannot touch rows another test is using.
_BACKFILL = """
UPDATE task t SET state = CASE
    WHEN NOT EXISTS (
        SELECT 1 FROM agent_run r
        WHERE r.task_id = t.id AND r.tenant_id = t.tenant_id AND r.state <> 'done'
    ) THEN 'done'
    ELSE 'failed'
END
WHERE t.tenant_id = :tenant
  AND t.state NOT IN ('done','failed','budget_exceeded')
  AND EXISTS (
      SELECT 1 FROM agent_run r WHERE r.task_id = t.id AND r.tenant_id = t.tenant_id
  )
  AND NOT EXISTS (
      SELECT 1 FROM agent_run r
      WHERE r.task_id = t.id AND r.tenant_id = t.tenant_id
        AND r.state IN ('queued','running','waiting_for_input','waiting_for_approval')
  )
"""


async def _task_with_runs(
    db: Any, tenant: uuid.UUID, *, task_state: str, run_states: list[str]
) -> uuid.UUID:
    if await db.get(m.Organization, tenant) is None:
        db.add(m.Organization(id=tenant, slug=f"t-{tenant.hex[:8]}", name="T"))
        await db.flush()
    dept = m.Department(tenant_id=tenant, name="Kundenservice", frame={})
    db.add(dept)
    await db.flush()
    agent = m.Agent(
        tenant_id=tenant,
        department_id=dept.id,
        name="Sina",
        status="idle",
        narrowing={},
        definition={},
        presentation={},
    )
    db.add(agent)
    await db.flush()
    task = m.Task(
        tenant_id=tenant,
        department_id=dept.id,
        assigned_agent_id=agent.id,
        title="t",
        state=task_state,
    )
    db.add(task)
    await db.flush()
    for state in run_states:
        db.add(
            m.AgentRun(
                tenant_id=tenant,
                agent_id=agent.id,
                task_id=task.id,
                state=state,
                context={},
            )
        )
    await db.flush()
    return task.id


@pytest.mark.parametrize(
    ("task_state", "run_states", "expected"),
    [
        # The 66: open behind a run that already failed.
        ("in_progress", ["failed"], "failed"),
        # Parked on an approval nobody will ever decide.
        ("waiting_for_approval", ["failed"], "failed"),
        ("waiting_for_input", ["interrupted"], "failed"),
        # The verdict is derived, not assumed.
        ("in_progress", ["done"], "done"),
        ("in_progress", ["done", "done"], "done"),
        ("in_progress", ["done", "failed"], "failed"),
        # Still being worked -- must be left exactly as it is.
        ("in_progress", ["running"], "in_progress"),
        ("in_progress", ["failed", "queued"], "in_progress"),
        ("in_progress", ["waiting_for_approval"], "in_progress"),
        # No run at all: unknown is not the same as done.
        ("in_progress", [], "in_progress"),
        # Already settled, and `budget_exceeded` must survive verbatim.
        ("budget_exceeded", ["failed"], "budget_exceeded"),
        ("done", ["failed"], "done"),
        ("failed", ["failed"], "failed"),
        # Never started, never run.
        ("backlog", [], "backlog"),
    ],
)
async def test_backfill_matches_the_runtime_rule(
    app_session: AppSessionFactory,
    task_state: str,
    run_states: list[str],
    expected: str,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        task_id = await _task_with_runs(db, tenant, task_state=task_state, run_states=run_states)

        await db.execute(text(_BACKFILL), {"tenant": tenant})

        after = await db.get(m.Task, task_id)
        assert after is not None
        # The UPDATE went round the ORM, so the identity map still holds the
        # pre-statement value until it is expired.
        await db.refresh(after)
        assert after.state == expected
