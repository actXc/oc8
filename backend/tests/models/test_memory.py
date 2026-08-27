from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from oc8 import models as m
from oc8.constants import ACME_TENANT_ID
from tests.conftest import AppSessionFactory


async def test_memory_record_status_defaults_to_approved(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        store = m.MemoryStore(tenant_id=tenant, tier="agent", owner_id=uuid.uuid4())
        db.add(store)
        await db.flush()
        record = m.MemoryRecord(
            tenant_id=tenant,
            store_id=store.id,
            content="a fact",
            written_by=uuid.uuid4(),
        )
        db.add(record)
        await db.flush()
        assert record.status == "approved"


async def test_memory_record_status_accepts_pending_and_rejected(
    app_session: AppSessionFactory,
) -> None:
    # Uses tier="department" with a fresh owner_id rather than tier="company"
    # (owner_id=tenant): company is a singleton store per tenant, and other
    # tests in the suite share the same ACME_TENANT_ID, so a raw insert
    # against the company store collides with theirs. This test only
    # exercises the status field, not tier semantics, so tier choice is free.
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        store = m.MemoryStore(tenant_id=tenant, tier="department", owner_id=uuid.uuid4())
        db.add(store)
        await db.flush()
        pending = m.MemoryRecord(
            tenant_id=tenant,
            store_id=store.id,
            content="pending fact",
            written_by=uuid.uuid4(),
            status="pending",
        )
        db.add(pending)
        await db.flush()
        assert pending.status == "pending"


async def test_memory_record_status_rejects_invalid_value(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        store = m.MemoryStore(tenant_id=tenant, tier="agent", owner_id=uuid.uuid4())
        db.add(store)
        await db.flush()
        bad = m.MemoryRecord(
            tenant_id=tenant,
            store_id=store.id,
            content="x",
            written_by=uuid.uuid4(),
            status="bogus",
        )
        db.add(bad)
        try:
            await db.flush()
            raise AssertionError("Expected IntegrityError to be raised")
        except IntegrityError:
            await db.rollback()


async def test_memory_store_unique_per_tenant_tier_owner(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    owner = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(m.MemoryStore(tenant_id=tenant, tier="agent", owner_id=owner))
        await db.flush()
        db.add(m.MemoryStore(tenant_id=tenant, tier="agent", owner_id=owner))
        try:
            await db.flush()
            raise AssertionError("Expected IntegrityError to be raised")
        except IntegrityError:
            await db.rollback()
