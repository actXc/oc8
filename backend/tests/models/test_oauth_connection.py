from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_grant_type_defaults_to_authorization_code(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        conn = m.OAuthConnection(
            tenant_id=tenant,
            provider="google",
            account_label="a@example.com",
            access_secret_ref="oauth/x/access",
            client_source="platform",
        )
        db.add(conn)
        await db.flush()
        conn_id = conn.id
    async with app_session(tenant) as db:
        loaded = await db.get(m.OAuthConnection, conn_id)
        assert loaded is not None
        assert loaded.grant_type == "authorization_code"
        assert loaded.azure_tenant_id is None


async def test_grant_type_accepts_client_credentials(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        conn = m.OAuthConnection(
            tenant_id=tenant,
            provider="microsoft",
            account_label="contoso",
            access_secret_ref="oauth/y/access",
            refresh_secret_ref="oauth/y/refresh",
            client_source="tenant",
            grant_type="client_credentials",
            azure_tenant_id="11111111-1111-1111-1111-111111111111",
        )
        db.add(conn)
        await db.flush()
        conn_id = conn.id
    async with app_session(tenant) as db:
        loaded = await db.get(m.OAuthConnection, conn_id)
        assert loaded is not None
        assert loaded.grant_type == "client_credentials"
        assert loaded.azure_tenant_id == "11111111-1111-1111-1111-111111111111"


async def test_grant_type_rejects_unknown_value(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(
            m.OAuthConnection(
                tenant_id=tenant,
                provider="google",
                account_label="a@example.com",
                access_secret_ref="oauth/z/access",
                client_source="platform",
                grant_type="not_a_real_grant",
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


async def test_grant_type_accepts_device_code_and_provider_metadata_defaults_empty(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        conn = m.OAuthConnection(
            tenant_id=tenant,
            provider="openai_chatgpt",
            account_label="test@example.com",
            access_secret_ref="oauth/device/access",
            client_source="tenant",
            grant_type="device_code",
        )
        db.add(conn)
        await db.flush()
        conn_id = conn.id
    async with app_session(tenant) as db:
        loaded = await db.get(m.OAuthConnection, conn_id)
        assert loaded is not None
        assert loaded.grant_type == "device_code"
        assert loaded.provider_metadata == {}


async def test_provider_metadata_round_trips(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        conn = m.OAuthConnection(
            tenant_id=tenant,
            provider="openai_chatgpt",
            account_label="test2@example.com",
            access_secret_ref="oauth/device2/access",
            client_source="tenant",
            grant_type="device_code",
            provider_metadata={"chatgpt_account_id": "acct_123"},
        )
        db.add(conn)
        await db.flush()
        conn_id = conn.id
    async with app_session(tenant) as db:
        loaded = await db.get(m.OAuthConnection, conn_id)
        assert loaded is not None
        assert loaded.provider_metadata == {"chatgpt_account_id": "acct_123"}
