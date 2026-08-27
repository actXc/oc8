from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from tests.conftest import AppSessionFactory

from oc8 import models as m

pytestmark = pytest.mark.asyncio


async def test_checkpoint_is_writable_and_updatable(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        s.add(
            m.AuditChainCheckpoint(
                tenant_id=tenant, last_seq=0, last_hash=b"\x00" * 32, status="ok"
            )
        )
        await s.flush()

        cp = (
            await s.execute(
                select(m.AuditChainCheckpoint).where(m.AuditChainCheckpoint.tenant_id == tenant)
            )
        ).scalar_one()
        cp.last_seq = 42
        cp.status = "broken"
        cp.broken_at_seq = 43
        await s.flush()

    async with app_session(tenant) as s:
        cp = (await s.execute(select(m.AuditChainCheckpoint))).scalar_one()
        assert (cp.last_seq, cp.status, cp.broken_at_seq) == (42, "broken", 43)


async def test_checkpoint_is_tenant_isolated(app_session: AppSessionFactory) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    async with app_session(a) as s:
        s.add(m.AuditChainCheckpoint(tenant_id=a, last_seq=7, last_hash=b"\x00" * 32))
        await s.flush()

    async with app_session(b) as s:
        assert (await s.execute(select(m.AuditChainCheckpoint))).scalars().all() == []
