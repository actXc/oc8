"""Skills backend (Design System Consistency plan, Task 3):
PATCH/DELETE(archive-or-hard)/restore, and the paged search/filter/group list
that replaced the old bare `list[SkillDTO]` GET /skills."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app

pytestmark = pytest.mark.asyncio


def _h(tenant: uuid.UUID, role: str = "org_admin") -> dict[str, str]:
    tok = get_identity_provider().mint(tenant_id=tenant, subject="author", role=role)
    return {"Authorization": f"Bearer {tok}"}


def _body(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "Invoice check",
        "description": "Checks invoices against the PO.",
        "category": "finance",
        "instructions": "Compare the invoice total to the purchase order.",
        "tools": [],
        "guardrails": [],
    }
    base.update(over)
    return base


async def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


# ---------------------------------------------------------------- update


async def test_patch_skill_updates_fields() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            skill_id = (await c.post("/api/v1/skills", json=_body(), headers=_h(tenant))).json()[
                "id"
            ]
            resp = await c.patch(
                f"/api/v1/skills/{skill_id}",
                json={"name": "Renamed", "description": "new desc"},
                headers=_h(tenant),
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["name"] == "Renamed"
            assert resp.json()["description"] == "new desc"


async def test_patch_skill_updates_instructions_on_current_version() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            skill_id = (await c.post("/api/v1/skills", json=_body(), headers=_h(tenant))).json()[
                "id"
            ]
            resp = await c.patch(
                f"/api/v1/skills/{skill_id}",
                json={"instructions": "New instructions entirely."},
                headers=_h(tenant),
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["instructions"] == "New instructions entirely."


async def test_patch_requires_admin() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            skill_id = (await c.post("/api/v1/skills", json=_body(), headers=_h(tenant))).json()[
                "id"
            ]
            resp = await c.patch(
                f"/api/v1/skills/{skill_id}",
                json={"name": "nope"},
                headers=_h(tenant, "member"),
            )
            assert resp.status_code == 403


async def test_patch_unknown_skill_is_404() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            resp = await c.patch(
                f"/api/v1/skills/{uuid.uuid4()}",
                json={"name": "nope"},
                headers=_h(tenant),
            )
            assert resp.status_code == 404


# ---------------------------------------------------------------- delete / archive / restore


async def test_delete_skill_with_zero_assignments_hard_deletes() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            skill_id = (await c.post("/api/v1/skills", json=_body(), headers=_h(tenant))).json()[
                "id"
            ]
            resp = await c.delete(f"/api/v1/skills/{skill_id}", headers=_h(tenant))
            assert resp.status_code == 200, resp.text
            assert resp.json()["outcome"] == "deleted"

            listed = await c.get("/api/v1/skills", headers=_h(tenant))
            assert skill_id not in [s["id"] for s in listed.json()["items"]]

            archived = await c.get("/api/v1/skills?includeArchived=true", headers=_h(tenant))
            assert skill_id not in [s["id"] for s in archived.json()["items"]]


async def test_delete_skill_with_assignment_archives_instead(app_session: Any) -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            created = (await c.post("/api/v1/skills", json=_body(), headers=_h(tenant))).json()
            skill_id = created["id"]
            version_id = created["currentVersionId"]

            async with app_session(tenant) as db:
                dept = m.Department(tenant_id=tenant, name="Finance", frame={})
                db.add(dept)
                await db.flush()
                agent = m.Agent(
                    tenant_id=tenant,
                    department_id=dept.id,
                    name="Clerk",
                    status="stopped",
                    narrowing={},
                    definition={},
                )
                db.add(agent)
                await db.flush()
                db.add(
                    m.SkillAssignment(
                        tenant_id=tenant,
                        agent_id=agent.id,
                        skill_version_id=uuid.UUID(version_id),
                        enabled=True,
                    )
                )
                await db.flush()

            resp = await c.delete(f"/api/v1/skills/{skill_id}", headers=_h(tenant))
            assert resp.status_code == 200, resp.text
            assert resp.json()["outcome"] == "archived"

            default = await c.get("/api/v1/skills", headers=_h(tenant))
            assert skill_id not in [s["id"] for s in default.json()["items"]]

            archived = await c.get("/api/v1/skills?includeArchived=true", headers=_h(tenant))
            ids = [s["id"] for s in archived.json()["items"]]
            assert skill_id in ids


async def test_restore_skill_clears_archive(app_session: Any) -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            skill_id = (await c.post("/api/v1/skills", json=_body(), headers=_h(tenant))).json()[
                "id"
            ]

            async with app_session(tenant) as db:
                skill = await db.get(m.Skill, uuid.UUID(skill_id))
                skill.deleted_at = dt.datetime.now(tz=dt.UTC)

            resp = await c.post(f"/api/v1/skills/{skill_id}/restore", headers=_h(tenant))
            assert resp.status_code == 200, resp.text

            default = await c.get("/api/v1/skills", headers=_h(tenant))
            assert skill_id in [s["id"] for s in default.json()["items"]]


async def test_restore_a_non_archived_skill_is_404() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            skill_id = (await c.post("/api/v1/skills", json=_body(), headers=_h(tenant))).json()[
                "id"
            ]
            resp = await c.post(f"/api/v1/skills/{skill_id}/restore", headers=_h(tenant))
            assert resp.status_code == 404


async def test_delete_requires_admin() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            skill_id = (await c.post("/api/v1/skills", json=_body(), headers=_h(tenant))).json()[
                "id"
            ]
            resp = await c.delete(f"/api/v1/skills/{skill_id}", headers=_h(tenant, "member"))
            assert resp.status_code == 403


# ---------------------------------------------------------------- list query


async def test_list_skills_search_filters_by_name_and_description() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            await c.post("/api/v1/skills", json=_body(name="Email Triage"), headers=_h(tenant))
            await c.post("/api/v1/skills", json=_body(name="Invoice Matching"), headers=_h(tenant))
            resp = await c.get("/api/v1/skills?search=email", headers=_h(tenant))
            names = [s["name"] for s in resp.json()["items"]]
            assert names == ["Email Triage"]


async def test_list_skills_filters_by_category_and_author() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            await c.post(
                "/api/v1/skills",
                json=_body(name="Finance one", category="finance"),
                headers=_h(tenant),
            )
            await c.post(
                "/api/v1/skills", json=_body(name="Ops one", category="ops"), headers=_h(tenant)
            )
            resp = await c.get("/api/v1/skills?category=ops", headers=_h(tenant))
            names = [s["name"] for s in resp.json()["items"]]
            assert names == ["Ops one"]

            resp2 = await c.get("/api/v1/skills?author=author", headers=_h(tenant))
            assert len(resp2.json()["items"]) == 2


async def test_list_skills_group_by_category() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            await c.post("/api/v1/skills", json=_body(name="B", category="ops"), headers=_h(tenant))
            await c.post(
                "/api/v1/skills", json=_body(name="A", category="sales"), headers=_h(tenant)
            )
            resp = await c.get("/api/v1/skills?group_by=category", headers=_h(tenant))
            assert resp.status_code == 200, resp.text
            categories = [s["category"] for s in resp.json()["items"]]
            assert categories == ["ops", "sales"]


async def test_list_skills_paginates_with_total_count() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            for i in range(5):
                await c.post("/api/v1/skills", json=_body(name=f"S{i}"), headers=_h(tenant))
            resp = await c.get("/api/v1/skills?limit=2&offset=1", headers=_h(tenant))
            body = resp.json()
            assert body["totalCount"] >= 5
            assert len(body["items"]) == 2


async def test_list_skills_excludes_archived_by_default_but_includes_when_asked(
    app_session: Any,
) -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            live_id = (await c.post("/api/v1/skills", json=_body(), headers=_h(tenant))).json()[
                "id"
            ]
            skill_id = (
                await c.post("/api/v1/skills", json=_body(name="Archived one"), headers=_h(tenant))
            ).json()["id"]

            async with app_session(tenant) as db:
                skill = await db.get(m.Skill, uuid.UUID(skill_id))
                skill.deleted_at = dt.datetime.now(tz=dt.UTC)

            default = await c.get("/api/v1/skills", headers=_h(tenant))
            assert skill_id not in [s["id"] for s in default.json()["items"]]
            live = next(s for s in default.json()["items"] if s["id"] == live_id)
            assert live["deletedAt"] is None

            archived = await c.get("/api/v1/skills?includeArchived=true", headers=_h(tenant))
            items = archived.json()["items"]
            assert skill_id in [s["id"] for s in items]
            archived_item = next(s for s in items if s["id"] == skill_id)
            assert archived_item["deletedAt"] is not None
