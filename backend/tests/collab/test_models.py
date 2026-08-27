from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from oc8 import models as m
from oc8.constants import ACME_TENANT_ID
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_handoff_type_and_handoff_persist_with_rls(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        ht = m.HandoffType(
            tenant_id=tenant,
            name="project.kickoff",
            payload_schema={"type": "object", "required": ["customer"]},
        )
        db.add(ht)
        await db.flush()
        h = m.Handoff(
            tenant_id=tenant,
            handoff_type_id=ht.id,
            source_department_id=uuid.uuid4(),
            target_department_id=uuid.uuid4(),
            payload={"customer": "Acme"},
            created_by=uuid.uuid4(),
        )
        db.add(h)
        await db.flush()
        h_id = h.id
        assert h.status == "pending" and h.gate == "auto"

    async with app_session(tenant) as db:
        loaded = await db.get(m.Handoff, h_id)
        assert loaded is not None and loaded.payload == {"customer": "Acme"}
        enabled = (
            await db.execute(text("SELECT relrowsecurity FROM pg_class WHERE relname='handoff'"))
        ).scalar_one()
    assert enabled is True
