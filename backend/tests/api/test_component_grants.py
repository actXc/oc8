"""GET/POST/DELETE /components/grants -- which agent or department may
render which governed UI component. Before this endpoint existed, there was
no way to create a ComponentGrant row at all, so render_component always
refused (see the "data_table not granted" report that surfaced this)."""

from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role="org_admin")
    return {"Authorization": f"Bearer {token}"}


async def _seed_agent(
    app_session: AppSessionFactory, tenant: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Support", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Lennart")
        db.add(agent)
        await db.flush()
        return dept.id, agent.id


async def test_create_department_grant_then_list_it(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    dept_id, _agent_id = await _seed_agent(app_session, tenant)

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/components/grants",
                json={
                    "componentKey": "data_table",
                    "granteeType": "department",
                    "granteeId": str(dept_id),
                },
                headers=_headers(tenant),
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["componentKey"] == "data_table"
            assert body["granteeType"] == "department"
            assert body["granteeId"] == str(dept_id)

            list_r = await c.get("/api/v1/components/grants", headers=_headers(tenant))
            assert list_r.status_code == 200, list_r.text
            assert any(g["id"] == body["id"] for g in list_r.json())


async def test_create_grant_is_idempotent(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    dept_id, _agent_id = await _seed_agent(app_session, tenant)

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            body = {
                "componentKey": "bar_chart",
                "granteeType": "department",
                "granteeId": str(dept_id),
            }
            r1 = await c.post("/api/v1/components/grants", json=body, headers=_headers(tenant))
            r2 = await c.post("/api/v1/components/grants", json=body, headers=_headers(tenant))
            assert r1.status_code == 201, r1.text
            assert r2.status_code == 201, r2.text
            assert r1.json()["id"] == r2.json()["id"]


async def test_create_grant_for_an_agent_directly(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    _dept_id, agent_id = await _seed_agent(app_session, tenant)

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/components/grants",
                json={
                    "componentKey": "record_card",
                    "granteeType": "agent",
                    "granteeId": str(agent_id),
                },
                headers=_headers(tenant),
            )
            assert r.status_code == 201, r.text
            assert r.json()["granteeType"] == "agent"


async def test_create_grant_rejects_an_unknown_component(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    dept_id, _agent_id = await _seed_agent(app_session, tenant)

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/components/grants",
                json={
                    "componentKey": "pie_chart",
                    "granteeType": "department",
                    "granteeId": str(dept_id),
                },
                headers=_headers(tenant),
            )
            assert r.status_code == 404, r.text


async def test_create_grant_rejects_a_department_that_does_not_exist(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/components/grants",
                json={
                    "componentKey": "data_table",
                    "granteeType": "department",
                    "granteeId": str(uuid.uuid4()),
                },
                headers=_headers(tenant),
            )
            assert r.status_code == 404, r.text


async def test_delete_grant_removes_it(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    dept_id, _agent_id = await _seed_agent(app_session, tenant)

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            create_r = await c.post(
                "/api/v1/components/grants",
                json={
                    "componentKey": "line_chart",
                    "granteeType": "department",
                    "granteeId": str(dept_id),
                },
                headers=_headers(tenant),
            )
            grant_id = create_r.json()["id"]

            del_r = await c.delete(
                f"/api/v1/components/grants/{grant_id}", headers=_headers(tenant)
            )
            assert del_r.status_code == 204, del_r.text

            list_r = await c.get("/api/v1/components/grants", headers=_headers(tenant))
            assert all(g["id"] != grant_id for g in list_r.json())


async def test_delete_missing_grant_is_404(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.delete(
                f"/api/v1/components/grants/{uuid.uuid4()}", headers=_headers(tenant)
            )
            assert r.status_code == 404, r.text
