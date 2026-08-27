from __future__ import annotations

import datetime as dt
import uuid

import pytest

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_round_trips_and_defaults_reported_cost_to_none(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        row = m.ModelCostReconciliation(
            tenant_id=tenant,
            provider="anthropic",
            report_date=dt.date.today(),
            oc8_calculated_cost_micros=1000,
        )
        db.add(row)
        await db.flush()
        assert row.provider_reported_cost_micros is None
