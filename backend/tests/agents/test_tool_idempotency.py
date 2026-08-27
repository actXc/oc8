"""Repeating a side-effectful tool call must not repeat the side effect.

Prerequisite for a self-driving runtime, not a nicety. Without checkpoints a
killed container restarts its task from the beginning, and for an agent that
writes to Odoo "from the beginning" means creating the quote a second time --
three identical quotes came out of exactly that kind of repetition on
2026-07-25. Idempotency is what makes restart-from-scratch survivable.

Scoped to the TASK, not the run: a resumed leg continues the same task but is
driven by a different run, so the task is the only key that survives a restart.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.tool_idempotency import (
    args_fingerprint,
    record_invocation,
    replayed_result,
)
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def _task(db: Any, tenant: uuid.UUID) -> m.Task:
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
        title="Erstelle ein Angebot", state="in_progress",
    )
    db.add(task)
    await db.flush()
    return task


def test_the_fingerprint_ignores_key_order_but_not_values() -> None:
    """A harness may serialise the same arguments differently between attempts;
    that must still count as the same call. Different values must not."""
    a = args_fingerprint({"model": "sale.order", "qty": 20})
    b = args_fingerprint({"qty": 20, "model": "sale.order"})
    c = args_fingerprint({"model": "sale.order", "qty": 21})
    assert a == b
    assert a != c


async def test_a_repeated_write_returns_the_first_result_without_acting_again(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        task = await _task(db, tenant)
        args = {"model": "sale.order", "values": {"partner_id": 7}}

        assert await replayed_result(
            db, tenant_id=tenant, task_id=task.id, tool="create_record", arguments=args
        ) is None

        await record_invocation(
            db, tenant_id=tenant, task_id=task.id, tool="create_record",
            arguments=args, result="created id=28",
        )

        again = await replayed_result(
            db, tenant_id=tenant, task_id=task.id, tool="create_record", arguments=args
        )
    assert again is not None
    assert "created id=28" in again
    # The model must be TOLD it is a replay, or it will report having created a
    # second quote that does not exist.
    assert "replay" in again.lower()


async def test_a_different_argument_is_a_different_call(
    app_session: AppSessionFactory,
) -> None:
    """Two genuinely different quotes in one task must both go through."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        task = await _task(db, tenant)
        await record_invocation(
            db, tenant_id=tenant, task_id=task.id, tool="create_record",
            arguments={"qty": 20}, result="id=1",
        )
        other = await replayed_result(
            db, tenant_id=tenant, task_id=task.id, tool="create_record",
            arguments={"qty": 21},
        )
    assert other is None


async def test_another_task_is_not_deduplicated_against_this_one(
    app_session: AppSessionFactory,
) -> None:
    """Two tasks legitimately create the same quote for the same customer."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        first = await _task(db, tenant)
        second = await _task(db, tenant)
        args = {"model": "sale.order", "values": {"partner_id": 7}}
        await record_invocation(
            db, tenant_id=tenant, task_id=first.id, tool="create_record",
            arguments=args, result="id=1",
        )
        assert await replayed_result(
            db, tenant_id=tenant, task_id=second.id, tool="create_record", arguments=args
        ) is None


async def test_recording_the_same_call_twice_does_not_explode(
    app_session: AppSessionFactory,
) -> None:
    """Two containers can race on a restart. The second writer must lose quietly
    rather than fail the run with a unique-violation."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        task = await _task(db, tenant)
        args = {"a": 1}
        await record_invocation(
            db, tenant_id=tenant, task_id=task.id, tool="create_record",
            arguments=args, result="first",
        )
        await record_invocation(
            db, tenant_id=tenant, task_id=task.id, tool="create_record",
            arguments=args, result="second",
        )
        got = await replayed_result(
            db, tenant_id=tenant, task_id=task.id, tool="create_record", arguments=args
        )
    assert got is not None
    # The FIRST result is what actually happened; the loser must not overwrite it.
    assert "first" in got
