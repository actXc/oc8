"""Budget's new $ fields round-trip and default to NULL."""

from __future__ import annotations

import uuid

import pytest

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_dollar_fields_default_to_none(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        b = m.Budget(
            tenant_id=tenant, department_id=None, soft_limit_tokens=100, hard_limit_tokens=200
        )
        db.add(b)
        await db.flush()
        assert b.dollar_budget_usd is None
        assert b.dollar_reference_provider is None
        assert b.dollar_reference_model is None


async def test_dollar_fields_round_trip(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        b = m.Budget(
            tenant_id=tenant,
            department_id=None,
            hard_limit_tokens=1000,
            dollar_budget_usd=50.0,
            dollar_reference_provider="anthropic",
            dollar_reference_model="claude-sonnet-4-5",
        )
        db.add(b)
        await db.flush()
        assert b.dollar_budget_usd == 50.0
        assert b.dollar_reference_provider == "anthropic"
