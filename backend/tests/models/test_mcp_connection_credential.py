from __future__ import annotations

import uuid

import pytest

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_mcp_connection_can_be_paired_with_a_credential(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        cred = m.Credential(
            tenant_id=tenant, name="Odoo User 1", credential_type="odoo_login",
            field_values={}, secret_refs={},
        )
        db.add(cred)
        await db.flush()
        conn = m.McpConnection(
            tenant_id=tenant,
            name="Odoo (User 1)",
            server_url="",
            transport="stdio",
            credential_id=cred.id,
        )
        db.add(conn)
        await db.flush()
        conn_id = conn.id

    async with app_session(tenant) as db:
        reloaded = await db.get(m.McpConnection, conn_id)
        assert reloaded is not None
        assert reloaded.credential_id == cred.id
        assert reloaded.department_id is None


async def test_mcp_connection_without_a_credential_still_works(
    app_session: AppSessionFactory,
) -> None:
    """The untouched, department-scoped OAuth path: credential_id stays NULL."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        conn = m.McpConnection(
            tenant_id=tenant, name="Google Workspace", server_url="", transport="stdio",
        )
        db.add(conn)
        await db.flush()
        conn_id = conn.id

    async with app_session(tenant) as db:
        reloaded = await db.get(m.McpConnection, conn_id)
        assert reloaded is not None
        assert reloaded.credential_id is None
