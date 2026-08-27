from __future__ import annotations

import uuid

import pytest

from oc8.collab.handoff import (
    HandoffStateError,
    PayloadInvalid,
    accept_handoff,
    complete_handoff,
    create_handoff,
    create_handoff_type,
    reject_handoff,
)
from oc8.constants import ACME_TENANT_ID
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

_SCHEMA = {
    "type": "object",
    "required": ["customer"],
    "properties": {"customer": {"type": "string"}},
}


async def test_create_validates_payload_and_lifecycle(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        ht = await create_handoff_type(
            db, tenant_id=tenant, name="collab.svc.kickoff", payload_schema=_SCHEMA
        )
        h = await create_handoff(
            db,
            tenant_id=tenant,
            handoff_type=ht,
            source_department_id=uuid.uuid4(),
            target_department_id=uuid.uuid4(),
            payload={"customer": "Acme"},
            created_by=uuid.uuid4(),
        )
        assert h.status == "pending"
        tt = uuid.uuid4()
        await accept_handoff(db, h, target_task_id=tt)
        assert h.status == "accepted" and h.target_task_id == tt
        await complete_handoff(db, h)
        assert h.status == "completed"


async def test_invalid_payload_rejected(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        ht = await create_handoff_type(
            db, tenant_id=tenant, name="collab.svc.kickoff2", payload_schema=_SCHEMA
        )
        with pytest.raises(PayloadInvalid):
            await create_handoff(
                db,
                tenant_id=tenant,
                handoff_type=ht,
                source_department_id=uuid.uuid4(),
                target_department_id=uuid.uuid4(),
                payload={"wrong": 1},
                created_by=uuid.uuid4(),
            )


async def test_illegal_transition_raises(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        ht = await create_handoff_type(
            db, tenant_id=tenant, name="collab.svc.kickoff3", payload_schema=_SCHEMA
        )
        h = await create_handoff(
            db,
            tenant_id=tenant,
            handoff_type=ht,
            source_department_id=uuid.uuid4(),
            target_department_id=uuid.uuid4(),
            payload={"customer": "Acme"},
            created_by=uuid.uuid4(),
        )
        await reject_handoff(db, h)
        with pytest.raises(HandoffStateError):
            await complete_handoff(db, h)  # rejected -> completed illegal
