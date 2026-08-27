from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from oc8 import models as m
from oc8.constants import ACME_TENANT_ID
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_contract_binding_persists_with_rls(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        b = m.ContractBinding(
            tenant_id=tenant,
            event_type="sales.deal.won",
            handoff_type_id=uuid.uuid4(),
            source_department_id=uuid.uuid4(),
            target_department_id=uuid.uuid4(),
            payload_map={"customer": "$.customer"},
        )
        db.add(b)
        await db.flush()
        bid = b.id
        assert b.gate == "auto"

    async with app_session(tenant) as db:
        loaded = await db.get(m.ContractBinding, bid)
        assert loaded is not None and loaded.event_type == "sales.deal.won"
        enabled = (
            await db.execute(
                text("SELECT relrowsecurity FROM pg_class WHERE relname='contract_binding'")
            )
        ).scalar_one()
    assert enabled is True
