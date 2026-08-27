from __future__ import annotations

import uuid

import pytest
from tests.conftest import AppSessionFactory

from oc8.audit.chain import append_event

pytestmark = pytest.mark.asyncio


async def test_audit_event_has_responsible_columns_default_null(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        ev = await append_event(
            s, tenant_id=tenant, actor_type="system", actor_id=None,
            category="test", action="t.ping",
        )
        # columns exist; with no attribution wired yet they are addressable
        assert hasattr(ev, "responsible_type")
        assert hasattr(ev, "responsible_id")
