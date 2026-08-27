"""ModelPrice ORM round-trip and the global-not-tenant-scoped contract."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_model_price_round_trips(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        row = m.ModelPrice(
            provider="anthropic",
            model_pattern="testpattern",
            price_in_usd_per_1m=1.0,
            price_out_usd_per_1m=2.0,
        )
        db.add(row)
        await db.flush()
        got = (
            await db.execute(
                select(m.ModelPrice).where(m.ModelPrice.model_pattern == "testpattern")
            )
        ).scalar_one()
        assert got.provider == "anthropic"
        assert got.active is True


async def test_seed_data_present(app_session: AppSessionFactory) -> None:
    # Confirms the seed migrations landed with the CORRECT pattern -- covers
    # the exact bug this whole plan exists to fix: claude-sonnet-4-5-* must
    # match a seeded, active row. (claude45sonnet, the original wrong pattern
    # from migration 0060, was corrected to claudesonnet45 by migration 0062,
    # which deactivates -- never deletes -- the wrong row; see that
    # migration's docstring.)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        rows = (
            (
                await db.execute(
                    select(m.ModelPrice).where(
                        m.ModelPrice.provider == "anthropic",
                        m.ModelPrice.model_pattern == "claudesonnet45",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].price_in_usd_per_1m == 3.0
        assert rows[0].active is True
