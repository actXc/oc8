from __future__ import annotations

import uuid

import pytest

from oc8.constants import ACME_TENANT_ID
from oc8.metering.budget import check_budget, current_month_tokens, get_budget, set_budget
from oc8.metering.usage import record_usage
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_set_budget_upserts_single_row(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    dept = uuid.uuid4()
    async with app_session(tenant) as db:
        await set_budget(
            db, tenant_id=tenant, department_id=dept,
            soft_limit_tokens=1000, hard_limit_tokens=2000,
        )
        updated = await set_budget(
            db, tenant_id=tenant, department_id=dept,
            soft_limit_tokens=1500, hard_limit_tokens=3000,
        )
        assert updated.soft_limit_tokens == 1500
        assert updated.hard_limit_tokens == 3000

        found = await get_budget(db, tenant_id=tenant, department_id=dept)
        assert found is not None
        assert found.id == updated.id


async def test_current_month_tokens_sums_department_scope(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    dept = uuid.uuid4()
    async with app_session(tenant) as db:
        await record_usage(
            db, tenant_id=tenant, request_id=uuid.uuid4(), model="m", provider="p",
            tokens_in=100, tokens_out=50, department_id=dept,
        )
        await record_usage(
            db, tenant_id=tenant, request_id=uuid.uuid4(), model="m", provider="p",
            tokens_in=10, tokens_out=5, department_id=dept,
        )
        total = await current_month_tokens(db, tenant_id=tenant, department_id=dept)
        assert total == 165


async def test_current_month_tokens_sums_tenant_wide(app_session: AppSessionFactory) -> None:
    # Fresh tenant -- see Global Constraints' test-collision hazard note.
    tenant = uuid.uuid4()
    dept_a, dept_b = uuid.uuid4(), uuid.uuid4()
    async with app_session(tenant) as db:
        await record_usage(
            db, tenant_id=tenant, request_id=uuid.uuid4(), model="m", provider="p",
            tokens_in=100, tokens_out=0, department_id=dept_a,
        )
        await record_usage(
            db, tenant_id=tenant, request_id=uuid.uuid4(), model="m", provider="p",
            tokens_in=50, tokens_out=0, department_id=dept_b,
        )
        total = await current_month_tokens(db, tenant_id=tenant, department_id=None)
        assert total == 150


async def test_check_budget_no_budget_configured_is_unlimited(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    dept = uuid.uuid4()
    async with app_session(tenant) as db:
        result = await check_budget(db, tenant_id=tenant, department_id=dept)
        assert result.soft_exceeded is False
        assert result.hard_exceeded is False


async def test_check_budget_department_hard_limit_exceeded(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    dept = uuid.uuid4()
    async with app_session(tenant) as db:
        await set_budget(
            db, tenant_id=tenant, department_id=dept,
            soft_limit_tokens=100, hard_limit_tokens=200,
        )
        await record_usage(
            db, tenant_id=tenant, request_id=uuid.uuid4(), model="m", provider="p",
            tokens_in=150, tokens_out=100, department_id=dept,
        )
        result = await check_budget(db, tenant_id=tenant, department_id=dept)
        assert result.soft_exceeded is True
        assert result.hard_exceeded is True


async def test_check_budget_department_soft_only_not_hard(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    dept = uuid.uuid4()
    async with app_session(tenant) as db:
        await set_budget(
            db, tenant_id=tenant, department_id=dept,
            soft_limit_tokens=50, hard_limit_tokens=200,
        )
        await record_usage(
            db, tenant_id=tenant, request_id=uuid.uuid4(), model="m", provider="p",
            tokens_in=60, tokens_out=0, department_id=dept,
        )
        result = await check_budget(db, tenant_id=tenant, department_id=dept)
        assert result.soft_exceeded is True
        assert result.hard_exceeded is False


async def test_check_budget_tenant_wide_governs_even_if_department_has_room(
    app_session: AppSessionFactory,
) -> None:
    # Fresh tenant -- see Global Constraints' test-collision hazard note.
    tenant = uuid.uuid4()
    dept = uuid.uuid4()
    async with app_session(tenant) as db:
        await set_budget(
            db, tenant_id=tenant, department_id=dept,
            soft_limit_tokens=None, hard_limit_tokens=1_000_000,
        )
        await set_budget(
            db, tenant_id=tenant, department_id=None,
            soft_limit_tokens=None, hard_limit_tokens=50,
        )
        await record_usage(
            db, tenant_id=tenant, request_id=uuid.uuid4(), model="m", provider="p",
            tokens_in=60, tokens_out=0, department_id=dept,
        )
        result = await check_budget(db, tenant_id=tenant, department_id=dept)
        assert result.hard_exceeded is True
