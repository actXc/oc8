from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from oc8.models import AgentRun, RunStateTransition
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_a_transition_row_can_be_written_and_read_back(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = AgentRun(
            id=uuid.uuid4(),
            tenant_id=tenant,
            agent_id=uuid.uuid4(),
            state="running",
            context={},
            messages=[],
        )
        db.add(run)
        await db.flush()
        row = RunStateTransition(
            id=uuid.uuid4(),
            tenant_id=tenant,
            run_id=run.id,
            from_state="queued",
            to_state="running",
            at=dt.datetime.now(dt.UTC),
        )
        db.add(row)
        run_id = run.id

    async with app_session(tenant) as db:
        rows = (
            (
                await db.execute(
                    select(RunStateTransition).where(RunStateTransition.run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].from_state == "queued"
        assert rows[0].to_state == "running"
