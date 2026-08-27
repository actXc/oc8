"""Task 6 of the Design System Consistency plan: `DELETE /agents/{id}` (hard-
delete or archive, depending on dependents), `POST /agents/{id}/restore`, and
`GET /agents` widened to the uniform search/filter/group/pagination contract
(spec §1.1) -- `Page[AgentDTO]` instead of a bare list.

Conventions follow `tests/api/test_agents_create_endpoint.py` and
`tests/api/test_agents_write_department_scoped.py`: no shared `client`/
`tenant_headers` fixtures exist in this suite, so each test builds its own
`AsyncClient` against a fresh `create_app()` and an `org_admin` bearer token
(tenant-wide, so the department-scoped write gate is never the thing under
test here -- that gate already has its own file).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="boss", role="org_admin")
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def test_delete_agent_with_runs_archives(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Nora")
        db.add(agent)
        await db.flush()
        db.add(m.AgentRun(tenant_id=tenant, agent_id=agent.id, context={}))
        await db.flush()
        agent_id = agent.id

    async with _http() as http:
        resp = await http.delete(f"/api/v1/agents/{agent_id}", headers=_headers(tenant))
        assert resp.status_code == 200, resp.text
        assert resp.json()["outcome"] == "archived"

        default = await http.get("/api/v1/agents", headers=_headers(tenant))
        assert default.status_code == 200, default.text
        assert str(agent_id) not in [a["id"] for a in default.json()["items"]]

        archived = await http.get("/api/v1/agents?includeArchived=true", headers=_headers(tenant))
        assert archived.status_code == 200, archived.text
        items = archived.json()["items"]
        assert str(agent_id) in [a["id"] for a in items]
        archived_item = next(a for a in items if a["id"] == str(agent_id))
        assert archived_item["deletedAt"] is not None


async def test_delete_agent_without_runs_hard_deletes(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="No Runs")
        db.add(agent)
        await db.flush()
        agent_id = agent.id

    async with _http() as http:
        resp = await http.delete(f"/api/v1/agents/{agent_id}", headers=_headers(tenant))
        assert resp.status_code == 200, resp.text
        assert resp.json()["outcome"] == "deleted"

        archived = await http.get("/api/v1/agents?includeArchived=true", headers=_headers(tenant))
        assert str(agent_id) not in [a["id"] for a in archived.json()["items"]]


async def test_restore_agent_clears_archive(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Archived Agent")
        db.add(agent)
        await db.flush()
        agent.deleted_at = dt.datetime.now(tz=dt.UTC)
        await db.flush()
        agent_id = agent.id

    async with _http() as http:
        resp = await http.post(f"/api/v1/agents/{agent_id}/restore", headers=_headers(tenant))
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == str(agent_id)

        default = await http.get("/api/v1/agents", headers=_headers(tenant))
        assert str(agent_id) in [a["id"] for a in default.json()["items"]]


async def test_restore_agent_that_is_not_archived_404s(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Live Agent")
        db.add(agent)
        await db.flush()
        agent_id = agent.id

    async with _http() as http:
        resp = await http.post(f"/api/v1/agents/{agent_id}/restore", headers=_headers(tenant))
        assert resp.status_code == 404, resp.text


async def test_list_agents_search_and_pagination(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
        db.add(dept)
        await db.flush()
        db.add_all(
            [
                m.Agent(tenant_id=tenant, department_id=dept.id, name="Triage Bot"),
                m.Agent(tenant_id=tenant, department_id=dept.id, name="Triage Helper"),
                m.Agent(tenant_id=tenant, department_id=dept.id, name="Billing Bot"),
            ]
        )
        await db.flush()

    async with _http() as http:
        resp = await http.get("/api/v1/agents?search=triage&limit=1", headers=_headers(tenant))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "totalCount" in body
        assert body["totalCount"] == 2
        assert len(body["items"]) == 1
        assert "triage" in body["items"][0]["name"].lower()


async def test_list_agents_default_page_shape_has_total_count(
    app_session: AppSessionFactory,
) -> None:
    """The default (unfiltered) list is now `Page[AgentDTO]`, not a bare list --
    pinned separately from the search/pagination test above so a regression to
    the old bare-list shape fails even when nobody filters or paginates."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
        db.add(dept)
        await db.flush()
        db.add(m.Agent(tenant_id=tenant, department_id=dept.id, name="Solo"))
        await db.flush()

    async with _http() as http:
        resp = await http.get("/api/v1/agents", headers=_headers(tenant))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["totalCount"] == 1
        assert body["items"][0]["name"] == "Solo"
