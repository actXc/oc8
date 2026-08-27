from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_a_component_grant_round_trips(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id = uuid.uuid4()
        grant = m.ComponentGrant(
            tenant_id=tenant,
            component_key="record_card",
            grantee_type="agent",
            grantee_id=agent_id,
        )
        db.add(grant)
        await db.flush()
        fetched = await db.get(m.ComponentGrant, grant.id)
        assert fetched is not None
        assert fetched.component_key == "record_card"
        assert fetched.grantee_id == agent_id


async def test_grantee_type_is_constrained(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(
            m.ComponentGrant(
                tenant_id=tenant,
                component_key="record_card",
                grantee_type="team",  # not department or agent
                grantee_id=uuid.uuid4(),
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


async def test_the_same_grant_cannot_be_recorded_twice(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    grantee_id = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(
            m.ComponentGrant(
                tenant_id=tenant,
                component_key="record_card",
                grantee_type="agent",
                grantee_id=grantee_id,
            )
        )
        await db.flush()
        db.add(
            m.ComponentGrant(
                tenant_id=tenant,
                component_key="record_card",
                grantee_type="agent",
                grantee_id=grantee_id,
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()
