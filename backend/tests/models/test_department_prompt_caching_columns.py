from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_department_prompt_caching_enabled_defaults_true(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Sales", goal="", frame={}, presentation={})
        db.add(dept)
        await db.flush()
        await db.refresh(dept)
        assert dept.prompt_caching_enabled is True


async def test_token_usage_record_cache_columns_default_to_zero_and_false(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        rec = m.TokenUsageRecord(
            tenant_id=tenant,
            request_id=uuid.uuid4(),
            model="claude-sonnet-4",
            provider="anthropic",
            tokens_in=100,
            tokens_out=50,
        )
        db.add(rec)
        await db.flush()
        await db.refresh(rec)
        assert rec.cache_hit is False
        assert rec.saved_tokens_in == 0
        assert rec.saved_tokens_out == 0


async def test_token_usage_record_can_record_a_cache_hit(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        rec = m.TokenUsageRecord(
            tenant_id=tenant,
            request_id=uuid.uuid4(),
            model="claude-sonnet-4",
            provider="anthropic",
            tokens_in=0,
            tokens_out=0,
            cache_hit=True,
            saved_tokens_in=100,
            saved_tokens_out=50,
        )
        db.add(rec)
        await db.flush()
        got = (
            await db.execute(select(m.TokenUsageRecord).where(m.TokenUsageRecord.id == rec.id))
        ).scalar_one()
        assert got.cache_hit is True
        assert got.saved_tokens_in == 100
        assert got.saved_tokens_out == 50
