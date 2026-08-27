from __future__ import annotations

import uuid

import pytest

from oc8 import models as m
from oc8.constants import ACME_TENANT_ID
from oc8.runtime.clarification import request_clarification
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
