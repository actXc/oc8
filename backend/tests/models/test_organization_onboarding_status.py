from __future__ import annotations

import uuid

import pytest

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_new_tenant_defaults_to_pending(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        org = m.Organization(id=tenant, slug=f"t-{tenant.hex[:8]}", name="T", settings={})
        db.add(org)
        await db.flush()
        await db.refresh(org)
        assert org.onboarding_status == "pending"
