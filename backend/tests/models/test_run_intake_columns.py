# backend/tests/models/test_run_intake_columns.py
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from oc8 import models as m
from tests.conftest import AppSessionFactory


async def test_defaults(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        run = m.AgentRun(tenant_id=tenant, agent_id=uuid.uuid4(), state="queued")
        db.add(run)
        await db.flush()
        assert run.source == "manual"
        assert run.coalesced_count == 0
        assert run.idempotency_key is None
        assert run.coalesce_key is None


async def test_idempotency_key_unique_per_tenant(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    key = "dup-key"
    async with app_session(tenant) as db:
        db.add(
            m.AgentRun(
                tenant_id=tenant, agent_id=uuid.uuid4(), state="queued", idempotency_key=key
            )
        )
        await db.flush()
        db.add(
            m.AgentRun(
                tenant_id=tenant, agent_id=uuid.uuid4(), state="queued", idempotency_key=key
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


async def test_idempotency_key_null_not_deduped(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        for _ in range(3):
            db.add(
                m.AgentRun(
                    tenant_id=tenant,
                    agent_id=uuid.uuid4(),
                    state="queued",
                    idempotency_key=None,
                )
            )
        await db.flush()


async def test_idempotency_key_scoped_to_tenant(app_session: AppSessionFactory) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    key = "shared-key"
    async with app_session(tenant_a) as db_a:
        db_a.add(
            m.AgentRun(
                tenant_id=tenant_a, agent_id=uuid.uuid4(), state="queued", idempotency_key=key
            )
        )
        await db_a.flush()

    async with app_session(tenant_b) as db_b:
        db_b.add(
            m.AgentRun(
                tenant_id=tenant_b, agent_id=uuid.uuid4(), state="queued", idempotency_key=key
            )
        )
        await db_b.flush()


async def test_source_check_constraint(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(m.AgentRun(tenant_id=tenant, agent_id=uuid.uuid4(), state="queued", source="bogus"))
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()
