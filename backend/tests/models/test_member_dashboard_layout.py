from __future__ import annotations

import uuid

import pytest

from oc8 import models as m
from tests.conftest import AppSessionFactory
from oc8.constants import ACME_TENANT_ID

pytestmark = pytest.mark.asyncio


async def test_member_dashboard_layout_round_trips_widgets_json(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        member = m.OrgMember(tenant_id=tenant, subject="layout-owner", subject_uuid=uuid.uuid4())
        db.add(member)
        await db.flush()
        row = m.MemberDashboardLayout(
            tenant_id=tenant,
            member_id=member.id,
            widgets=[{"id": "w1", "type": "chat", "x": 0, "y": 0, "w": 6, "h": 6, "config": {}}],
            template_id="focus-chat",
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        assert row.widgets == [
            {"id": "w1", "type": "chat", "x": 0, "y": 0, "w": 6, "h": 6, "config": {}}
        ]
        assert row.template_id == "focus-chat"


async def test_member_dashboard_layout_widgets_defaults_to_empty_list(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        member = m.OrgMember(tenant_id=tenant, subject="layout-default", subject_uuid=uuid.uuid4())
        db.add(member)
        await db.flush()
        row = m.MemberDashboardLayout(tenant_id=tenant, member_id=member.id)
        db.add(row)
        await db.flush()
        await db.refresh(row)
        assert row.widgets == []
        assert row.template_id is None
