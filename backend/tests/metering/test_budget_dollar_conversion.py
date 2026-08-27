"""$ -> tokens budget conversion (metering/budget.py). Reference-model
resolution: most-used-in-scope-over-30d -> Copilot model -> reject. The
conversion happens once, at write time; check_budget's enforcement logic
(tested here too) must remain unchanged by this feature."""

from __future__ import annotations

import uuid

import pytest

from oc8 import models as m
from oc8.metering.budget import check_budget, convert_dollars_to_tokens
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_converts_using_the_most_used_model_in_scope(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    dept_id = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(
            id=dept_id, tenant_id=tenant, name="Sales", goal="", frame={}, presentation={}
        )
        db.add(dept)
        db.add(
            m.ModelPrice(
                provider="anthropic",
                model_pattern="claudesonnet4",
                price_in_usd_per_1m=3.0,
                price_out_usd_per_1m=15.0,
            )
        )
        # 1000 tokens on this model in-scope, recorded now (within 30 days).
        db.add(
            m.TokenUsageRecord(
                tenant_id=tenant,
                request_id=uuid.uuid4(),
                model="claude-sonnet-4",
                provider="anthropic",
                tokens_in=800,
                tokens_out=200,
                department_id=dept_id,
            )
        )
        # flush (not commit): a mid-block commit resets the RLS-scoped
        # app.tenant_id GUC (it is set is_local=true), which would make the
        # very next read see zero rows under RLS. flush keeps the inserts
        # visible within this same transaction/GUC scope.
        await db.flush()
        tokens, provider, model = await convert_dollars_to_tokens(
            db, tenant_id=tenant, department_id=dept_id, dollar_amount=90.0
        )
        # blended price = (3.0 + 15.0) / 2 = 9.0 $/1M tokens = 9.0 micro-$/token
        # 90.0 USD = 90_000_000 micros / 9.0 micros-per-token = 10_000_000 tokens
        assert tokens == 10_000_000
        assert provider == "anthropic"
        assert model == "claude-sonnet-4"


async def test_falls_back_to_copilot_model_when_scope_has_no_usage(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(
            m.ModelPrice(
                provider="anthropic",
                model_pattern="claudesonnet4",
                price_in_usd_per_1m=3.0,
                price_out_usd_per_1m=15.0,
            )
        )
        db.add(
            m.ModelConfig(
                tenant_id=tenant,
                provider="anthropic",
                model="claude-sonnet-4",
                locality="cloud",
                used_by_copilot=True,
            )
        )
        await db.flush()  # see flush note above -- avoid mid-block commit's GUC reset
        tokens, provider, _model = await convert_dollars_to_tokens(
            db, tenant_id=tenant, department_id=None, dollar_amount=9.0
        )
        assert tokens == 1_000_000
        assert provider == "anthropic"


async def test_rejects_when_no_reference_model_exists(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        with pytest.raises(ValueError, match="no reference model"):
            await convert_dollars_to_tokens(
                db, tenant_id=tenant, department_id=None, dollar_amount=50.0
            )


async def test_check_budget_logic_is_unchanged_by_this_feature(
    app_session: AppSessionFactory,
) -> None:
    """Regression guard: this feature must never touch check_budget's pure
    token comparison, even for a budget that was set in $ mode."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        budget = m.Budget(
            tenant_id=tenant,
            department_id=None,
            hard_limit_tokens=100,
            dollar_budget_usd=9.0,
            dollar_reference_provider="anthropic",
            dollar_reference_model="claude-sonnet-4",
        )
        db.add(budget)
        db.add(
            m.TokenUsageRecord(
                tenant_id=tenant,
                request_id=uuid.uuid4(),
                model="claude-sonnet-4",
                provider="anthropic",
                tokens_in=90,
                tokens_out=20,
            )
        )
        await db.flush()  # see flush note above -- avoid mid-block commit's GUC reset
        result = await check_budget(db, tenant_id=tenant, department_id=None)
        assert result.hard_exceeded is True  # 110 tokens used >= 100 hard limit -- pure token math
