from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_push_subscription_round_trips(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        member = m.OrgMember(tenant_id=tenant, subject="u1", subject_uuid=uuid.uuid4())
        db.add(member)
        await db.flush()
        sub = m.PushSubscription(
            tenant_id=tenant,
            member_id=member.id,
            endpoint="https://push.example.com/v1/abc",
            p256dh="p256dh-key",
            auth="auth-key",
            user_agent="pytest",
        )
        db.add(sub)
        await db.flush()
        sub_id = sub.id
    async with app_session(tenant) as db:
        loaded = await db.get(m.PushSubscription, sub_id)
        assert loaded is not None
        assert loaded.member_id == member.id
        assert loaded.endpoint == "https://push.example.com/v1/abc"
        assert loaded.p256dh == "p256dh-key"
        assert loaded.auth == "auth-key"
        assert loaded.user_agent == "pytest"


async def test_push_subscription_endpoint_is_globally_unique(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        member = m.OrgMember(tenant_id=tenant, subject="u1", subject_uuid=uuid.uuid4())
        db.add(member)
        await db.flush()
        db.add(
            m.PushSubscription(
                tenant_id=tenant,
                member_id=member.id,
                endpoint="https://push.example.com/v1/dupe",
                p256dh="a",
                auth="b",
            )
        )
        await db.flush()
        db.add(
            m.PushSubscription(
                tenant_id=tenant,
                member_id=member.id,
                endpoint="https://push.example.com/v1/dupe",
                p256dh="c",
                auth="d",
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()
