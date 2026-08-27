from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


async def test_run_cancellation_is_tenant_scoped(app_session: AppSessionFactory) -> None:
    """RLS: a row recorded under tenant A is invisible to tenant B's session."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    run_id = uuid.uuid4()

    async with app_session(tenant_a) as s:
        s.add(
            m.RunCancellation(
                tenant_id=tenant_a,
                run_id=run_id,
                requested_at=_now(),
                cancellation_kind="operator_interrupted",
            )
        )

    async with app_session(tenant_b) as s:
        rows = (
            await s.execute(select(m.RunCancellation).where(m.RunCancellation.run_id == run_id))
        ).scalars().all()
        assert rows == []

    async with app_session(tenant_a) as s:
        rows = (
            await s.execute(select(m.RunCancellation).where(m.RunCancellation.run_id == run_id))
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].cancellation_kind == "operator_interrupted"


async def test_run_cancellation_run_id_is_unique(app_session: AppSessionFactory) -> None:
    """One cancel row per run: a second insert for the same run_id violates the
    unique constraint (the endpoint relies on this to be idempotent)."""
    tenant = uuid.uuid4()
    run_id = uuid.uuid4()

    async with app_session(tenant) as s:
        s.add(
            m.RunCancellation(
                tenant_id=tenant, run_id=run_id, requested_at=_now(),
                cancellation_kind="operator_interrupted",
            )
        )

    with pytest.raises(IntegrityError):
        async with app_session(tenant) as s:
            s.add(
                m.RunCancellation(
                    tenant_id=tenant, run_id=run_id, requested_at=_now(),
                    cancellation_kind="operator_interrupted",
                )
            )
            await s.flush()
