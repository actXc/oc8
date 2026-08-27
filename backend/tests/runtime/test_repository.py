from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from oc8.constants import ACME_TENANT_ID
from oc8.runtime.repository import RunRepository
from oc8.runtime.states import InvalidTransition, RunState
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_create_and_get(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    agent_id = uuid.uuid4()
    async with app_session(tenant) as s:
        repo = RunRepository(s)
        run = await repo.create(tenant_id=tenant, agent_id=agent_id, context={"task": "x"})
        assert run.state == RunState.QUEUED.value
        fetched = await repo.get(run.id)
        assert fetched is not None and fetched.id == run.id


async def test_transition_validates(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as s:
        repo = RunRepository(s)
        run = await repo.create(tenant_id=tenant, agent_id=uuid.uuid4(), context={})
        await repo.transition(run, RunState.RUNNING)
        assert run.state == RunState.RUNNING.value
        await repo.transition(run, RunState.DONE)
        with pytest.raises(InvalidTransition):
            await repo.transition(run, RunState.RUNNING)


async def test_a_second_closer_in_another_transaction_does_not_close_it_again(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two processes may now legitimately decide the same run is over at the
    same moment, and only one of them may act.

    Since 2026-08-02 the reconciler's sweep and the queue's reclaim share one
    window (LEASE_LOST_AFTER_MS is ABANDONED_AFTER), so on a dead worker both
    fire within a beat of each other -- where the old five-minute reclaim always
    won by construction. The terminal->terminal guard below this could not see
    that: it compared `run.state` as THIS session read it, which is stale the
    moment another transaction commits, and the UPDATE carried no state
    predicate. Both closers passed the guard and both committed, so the run was
    counted failed twice, its claims released twice, its task closed twice, and
    whichever error message landed second erased the other.

    The claim release is what this asserts on, because it is the one with a real
    cost: a released claim is a record another agent may take, and releasing it
    twice releases the SECOND holder's claim.
    """
    released: list[uuid.UUID] = []

    async def _count(db: AsyncSession, *, tenant_id: uuid.UUID, run_id: uuid.UUID) -> int:
        released.append(run_id)
        return 0

    monkeypatch.setattr("oc8.agent.claims.release_run_claims", _count)

    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        run = await RunRepository(s).create(
            tenant_id=tenant, agent_id=uuid.uuid4(), context={"task": "x"}
        )
        await RunRepository(s).transition(run, RunState.RUNNING)
        run_id = run.id

    async with app_session(tenant) as slow:
        # The loser reads the run BEFORE the winner acts -- the whole point is
        # that its copy goes stale under it, which is what two workers a
        # network apart always look like.
        stale = await RunRepository(slow).get(run_id)
        assert stale is not None and stale.state == RunState.RUNNING.value

        async with app_session(tenant) as winner:
            fresh = await RunRepository(winner).get(run_id)
            assert fresh is not None
            await RunRepository(winner).transition(fresh, RunState.FAILED)
        # The winner has committed by here.

        await RunRepository(slow).transition(stale, RunState.FAILED)

    assert released == [run_id], "the run was closed twice"
