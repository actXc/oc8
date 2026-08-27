from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from oc8 import models as m
from oc8.constants import ACME_TENANT_ID
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_flow_version_run_persist_with_rls(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        flow = m.Flow(tenant_id=tenant, name="quote-to-cash")
        db.add(flow)
        await db.flush()
        ver = m.FlowVersion(
            tenant_id=tenant,
            flow_id=flow.id,
            semver="1.0.0",
            spec={"id": "q2c", "version": "1.0.0", "trigger": {}, "stages": []},
            artifact_hash=b"\x00" * 32,
        )
        db.add(ver)
        await db.flush()
        flow.current_version_id = ver.id
        run = m.FlowRun(tenant_id=tenant, flow_version_id=ver.id, context={"customer": "Acme"})
        db.add(run)
        await db.flush()
        run_id = run.id
        assert run.status == "running"

    async with app_session(tenant) as db:
        loaded = await db.get(m.FlowRun, run_id)
        assert loaded is not None and loaded.context == {"customer": "Acme"}
        enabled = (
            await db.execute(text("SELECT relrowsecurity FROM pg_class WHERE relname='flow_run'"))
        ).scalar_one()
    assert enabled is True
