"""Regression: ACME seed must not leave empty MCP connection stubs behind.

Empty `mcp_connection` rows (no credentials, never tested) render as
"Untested / Test connection" cards on Capas next to the real installed
capas. The demo seed deliberately omits them — agent Tools still use
department frame keys (hubspot, odoo, …) without needing connection rows.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from oc8 import models as m
from oc8.seed import DEPT_FRAMES, _seed_acme, det
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_seeded_acme_has_no_mcp_connection_stubs(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    fresh_tid = uuid.uuid4()
    monkeypatch.setattr("oc8.seed.ACME_TENANT_ID", fresh_tid)

    async with app_session(fresh_tid) as session:
        await _seed_acme(session)
        n = (
            await session.execute(
                select(func.count())
                .select_from(m.McpConnection)
                .where(m.McpConnection.tenant_id == fresh_tid)
            )
        ).scalar_one()
        dept = (
            await session.execute(
                select(m.Department).where(
                    m.Department.tenant_id == fresh_tid,
                    m.Department.id == det(fresh_tid, "dept", "vertrieb"),
                )
            )
        ).scalar_one()

    assert n == 0
    tools = (dept.frame or {}).get("tools") or {}
    assert "hubspot" in tools
    assert "demo-fs" not in tools
    assert set(DEPT_FRAMES["vertrieb"]) <= set(tools)
