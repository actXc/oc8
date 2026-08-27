from __future__ import annotations

import uuid

import pytest

from oc8 import models as m
from oc8.collab.contracts import ContractViolation
from oc8.collab.emit import emit_event
from oc8.collab.handoff import create_handoff_type
from oc8.constants import ACME_TENANT_ID
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_emit_creates_handoff_when_contracts_match(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        sales = m.Department(
            tenant_id=tenant, name="sales-emit", frame={"emits": ["sales.deal.won"]}
        )
        eng = m.Department(
            tenant_id=tenant,
            name="eng-emit",
            frame={
                "intakes": [{"handoff_type": "emit.kickoff", "route": "team_lead", "gate": "auto"}]
            },
        )
        db.add_all([sales, eng])
        await db.flush()
        ht = await create_handoff_type(
            db,
            tenant_id=tenant,
            name="emit.kickoff",
            payload_schema={"type": "object", "required": ["customer"]},
        )
        db.add(
            m.ContractBinding(
                tenant_id=tenant,
                event_type="sales.deal.won",
                handoff_type_id=ht.id,
                source_department_id=sales.id,
                target_department_id=eng.id,
                payload_map={"customer": "$.customer"},
            )
        )
        await db.flush()

        handoffs = await emit_event(
            db,
            tenant_id=tenant,
            source_department=sales,
            event_type="sales.deal.won",
            payload={"customer": "Acme", "extra": 1},
            created_by=uuid.uuid4(),
        )
        assert len(handoffs) == 1
        assert handoffs[0].payload == {"customer": "Acme"}  # mapped, not the raw event
        assert handoffs[0].target_department_id == eng.id


async def test_emit_undeclared_event_denied(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        sales = m.Department(tenant_id=tenant, name="sales-emit2", frame={"emits": []})
        db.add(sales)
        await db.flush()
        with pytest.raises(ContractViolation):
            await emit_event(
                db,
                tenant_id=tenant,
                source_department=sales,
                event_type="sales.deal.won",
                payload={},
                created_by=uuid.uuid4(),
            )
