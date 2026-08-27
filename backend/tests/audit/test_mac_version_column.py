from __future__ import annotations

import uuid

import pytest
from tests.conftest import AppSessionFactory

from oc8.audit.chain import append_event

pytestmark = pytest.mark.asyncio


async def test_new_rows_default_to_unkeyed(app_session: AppSessionFactory) -> None:
    """With the flag off (the default), rows are written at mac_version 0."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        ev = await append_event(
            s,
            tenant_id=tenant,
            actor_type="system",
            actor_id=None,
            category="test",
            action="t.ping",
        )
        assert ev.mac_version == 0
