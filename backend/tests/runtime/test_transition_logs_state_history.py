from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from oc8.constants import ACME_TENANT_ID
from oc8.models import RunStateTransition
from oc8.runtime.repository import RunRepository
from oc8.runtime.states import RunState
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_a_full_run_sequence_produces_the_expected_transition_rows(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as s:
        repo = RunRepository(s)
        run = await repo.create(tenant_id=tenant, agent_id=uuid.uuid4(), context={})

        # queued -> running -> waiting_for_approval -> running -> done
        await repo.transition(run, RunState.RUNNING)
        await repo.transition(run, RunState.WAITING_FOR_APPROVAL)
        await repo.transition(run, RunState.RUNNING)
        await repo.transition(run, RunState.DONE)

        rows = (
            (
                await s.execute(
                    select(RunStateTransition)
                    .where(RunStateTransition.run_id == run.id)
                    .order_by(RunStateTransition.at)
                )
            )
            .scalars()
            .all()
        )
        assert [(r.from_state, r.to_state) for r in rows] == [
            ("queued", "running"),
            ("running", "waiting_for_approval"),
            ("waiting_for_approval", "running"),
            ("running", "done"),
        ]
