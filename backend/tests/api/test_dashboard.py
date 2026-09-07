from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _token(subject: str = "dashboard-user", tenant: uuid.UUID | None = None) -> str:
    return get_identity_provider().mint(
        tenant_id=tenant or uuid.UUID(str(ACME_TENANT_ID)), subject=subject, role="member"
    )


async def test_get_layout_returns_null_when_the_caller_has_no_row() -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(subject='no-layout-yet')}"}
            r = await client.get("/api/v1/dashboard/layout", headers=headers)
            assert r.status_code == 200
            assert r.json() is None


async def test_put_layout_creates_then_get_returns_the_stored_shape() -> None:
    app = create_app()
    body = {
        "widgets": [
            {"id": "w1", "type": "chat", "x": 0, "y": 0, "w": 6, "h": 6, "config": {}},
        ],
        "templateId": "focus-chat",
    }
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(subject='put-create')}"}
            put_r = await client.put("/api/v1/dashboard/layout", json=body, headers=headers)
            assert put_r.status_code == 200, put_r.text
            assert put_r.json()["widgets"] == body["widgets"]
            assert put_r.json()["templateId"] == "focus-chat"

            get_r = await client.get("/api/v1/dashboard/layout", headers=headers)
            assert get_r.status_code == 200
            assert get_r.json()["widgets"] == body["widgets"]
            assert get_r.json()["templateId"] == "focus-chat"


async def test_put_layout_fully_replaces_not_merges(app_session: AppSessionFactory) -> None:
    app = create_app()
    first = {
        "widgets": [{"id": "w1", "type": "chat", "x": 0, "y": 0, "w": 6, "h": 6, "config": {}}],
        "templateId": None,
    }
    second = {
        "widgets": [
            {"id": "w2", "type": "budget", "x": 0, "y": 0, "w": 3, "h": 3, "config": {}},
        ],
        "templateId": None,
    }
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(subject='put-replace')}"}
            await client.put("/api/v1/dashboard/layout", json=first, headers=headers)
            r = await client.put("/api/v1/dashboard/layout", json=second, headers=headers)
            assert r.status_code == 200
            assert r.json()["widgets"] == second["widgets"]

    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        member = (
            await db.execute(select(m.OrgMember).where(m.OrgMember.subject == "put-replace"))
        ).scalar_one()
        row = (
            await db.execute(
                select(m.MemberDashboardLayout).where(
                    m.MemberDashboardLayout.member_id == member.id
                )
            )
        ).scalar_one()
        assert row.widgets == second["widgets"]


async def test_dashboard_layout_is_isolated_per_member() -> None:
    app = create_app()
    body_a = {
        "widgets": [{"id": "wa", "type": "chat", "x": 0, "y": 0, "w": 6, "h": 6, "config": {}}],
        "templateId": None,
    }
    body_b = {
        "widgets": [
            {"id": "wb", "type": "reports", "x": 0, "y": 0, "w": 4, "h": 4, "config": {}},
        ],
        "templateId": None,
    }
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers_a = {"Authorization": f"Bearer {_token(subject='member-a')}"}
            headers_b = {"Authorization": f"Bearer {_token(subject='member-b')}"}
            await client.put("/api/v1/dashboard/layout", json=body_a, headers=headers_a)
            await client.put("/api/v1/dashboard/layout", json=body_b, headers=headers_b)

            get_a = await client.get("/api/v1/dashboard/layout", headers=headers_a)
            get_b = await client.get("/api/v1/dashboard/layout", headers=headers_b)
            assert get_a.json()["widgets"] == body_a["widgets"]
            assert get_b.json()["widgets"] == body_b["widgets"]


async def test_get_templates_returns_exactly_three_with_widgets() -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(subject='templates-reader')}"}
            r = await client.get("/api/v1/dashboard/templates", headers=headers)
            assert r.status_code == 200
            templates = r.json()
            assert len(templates) == 3
            for template in templates:
                assert template["widgets"], template["id"]
                assert set(template["name"]) == {"en", "de"}
