from __future__ import annotations

import uuid

import pytest

from oc8 import models as m
from oc8.constants import ACME_TENANT_ID
from oc8.runtime.clarification import RepeatedClarification, request_clarification
from oc8.runtime.repository import RunRepository
from oc8.runtime.states import RunState
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_request_clarification_suspends_run(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="A")
        db.add(agent)
        await db.flush()
        run = await RunRepository(db).create(tenant_id=tenant, agent_id=agent.id, context={})
        await RunRepository(db).transition(run, RunState.RUNNING)

        c = await request_clarification(db, run=run, question="Which Odoo version?")
        assert c.status == "open"
        assert c.question == "Which Odoo version?"
        assert run.state == RunState.WAITING_FOR_INPUT.value


async def test_request_clarification_rejects_verbatim_repeat(
    app_session: AppSessionFactory,
) -> None:
    # A run whose Q&A history already carries an answer to this exact question
    # -- i.e. it was asked and answered once already on this same run -- must
    # not be allowed to park the human on it a second time.
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="A")
        db.add(agent)
        await db.flush()
        run = await RunRepository(db).create(
            tenant_id=tenant,
            agent_id=agent.id,
            context={
                "clarifications": [
                    {"question": "Which Odoo version?", "answer": "18.0"},
                ]
            },
        )
        await RunRepository(db).transition(run, RunState.RUNNING)

        with pytest.raises(RepeatedClarification):
            await request_clarification(db, run=run, question="Which Odoo version?")

        # No new Clarification row was created, and the run was NOT parked a
        # second time -- the caller (executor.py) is the one that fails it.
        assert run.state == RunState.RUNNING.value


async def test_request_clarification_allows_a_different_question(
    app_session: AppSessionFactory,
) -> None:
    # The repeat check is per QUESTION TEXT, not "has this run ever asked
    # anything before" -- a second, genuinely different question still parks
    # normally.
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="A")
        db.add(agent)
        await db.flush()
        run = await RunRepository(db).create(
            tenant_id=tenant,
            agent_id=agent.id,
            context={"clarifications": [{"question": "Which Odoo version?", "answer": "18.0"}]},
        )
        await RunRepository(db).transition(run, RunState.RUNNING)

        c = await request_clarification(db, run=run, question="Which team owns this?")
        assert c.status == "open"
        assert run.state == RunState.WAITING_FOR_INPUT.value
