from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _conn(tenant: uuid.UUID, label: str = "a@example.com") -> m.OAuthConnection:
    cid = uuid.uuid4()
    return m.OAuthConnection(
        id=cid,
        tenant_id=tenant,
        provider="google",
        account_label=label,
        scopes=["openid", "email"],
        access_secret_ref=f"oauth/{cid}/access",
        refresh_secret_ref=f"oauth/{cid}/refresh",
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        status="active",
        client_source="tenant",
    )


async def test_roundtrip_under_rls(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        row = _conn(tenant)
        db.add(row)
        await db.flush()
        got = (
            await db.execute(select(m.OAuthConnection).where(m.OAuthConnection.id == row.id))
        ).scalar_one()
        assert got.provider == "google"
        assert got.scopes == ["openid", "email"]
        assert got.status == "active"
        assert got.client_source == "tenant"


async def test_invisible_across_tenants(app_session: AppSessionFactory) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    async with app_session(tenant_a) as db:
        db.add(_conn(tenant_a))
        await db.flush()
        await db.commit()
    async with app_session(tenant_b) as db:
        rows = (await db.execute(select(m.OAuthConnection))).scalars().all()
        assert rows == []


async def test_account_is_unique_per_tenant_and_provider(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(_conn(tenant, "dup@example.com"))
        await db.flush()
        db.add(_conn(tenant, "dup@example.com"))
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


async def test_status_check_constraint_rejects_garbage(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        row = _conn(tenant)
        row.status = "banana"
        db.add(row)
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


async def test_data_source_carries_an_oauth_connection_id(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        conn = _conn(tenant)
        db.add(conn)
        await db.flush()
        ds = m.DataSource(
            tenant_id=tenant,
            connector_type="gdrive",
            name="drive",
            config={},
            oauth_connection_id=conn.id,
        )
        db.add(ds)
        await db.flush()
        assert ds.oauth_connection_id == conn.id
