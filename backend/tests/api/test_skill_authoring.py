"""Authoring skills over HTTP: create, version, list assignments, unassign."""

from __future__ import annotations

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
        "tools": ["erp.read"],
        "guardrails": ["Never approve above 10k without a human."],
    }
    base.update(over)
    return base


async def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


# ---------------------------------------------------------------- create

async def test_creating_a_skill_persists_it_and_it_comes_back_in_the_list() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            r = await c.post("/api/v1/skills", json=_body(), headers=_h(tenant))
            assert r.status_code == 201, r.text
            created = r.json()
            assert created["name"] == "Invoice check"
            assert created["version"] == "0.1.0"
            assert created["instructions"].startswith("Compare the invoice")
            assert created["tools"] == ["erp.read"]

            lst = await c.get("/api/v1/skills", headers=_h(tenant))
            assert any(s["id"] == created["id"] for s in lst.json()["items"])


async def test_a_created_skill_is_not_visible_to_another_tenant() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            r = await c.post("/api/v1/skills", json=_body(), headers=_h(tenant_a))
            skill_id = r.json()["id"]
            lst = await c.get("/api/v1/skills", headers=_h(tenant_b))
            assert not any(s["id"] == skill_id for s in lst.json()["items"])


async def test_a_skill_without_instructions_is_rejected() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            r = await c.post(
                "/api/v1/skills", json=_body(instructions="   "), headers=_h(tenant)
            )
            assert r.status_code == 400, r.text


async def test_creating_a_skill_requires_admin() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            r = await c.post("/api/v1/skills", json=_body(), headers=_h(tenant, "member"))
            assert r.status_code == 403


async def test_a_duplicate_name_is_rejected_rather_than_silently_shadowing() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            assert (
                await c.post("/api/v1/skills", json=_body(), headers=_h(tenant))
            ).status_code == 201
            again = await c.post("/api/v1/skills", json=_body(), headers=_h(tenant))
            assert again.status_code == 409


# ---------------------------------------------------------------- versions

async def test_publishing_a_new_version_moves_the_current_pointer() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            skill_id = (
                await c.post("/api/v1/skills", json=_body(), headers=_h(tenant))
            ).json()["id"]

            v2 = await c.post(
                f"/api/v1/skills/{skill_id}/versions",
                json={"semver": "0.2.0", "instructions": "Now also check the delivery note."},
                headers=_h(tenant),
            )
            assert v2.status_code == 201, v2.text
            assert v2.json()["version"] == "0.2.0"

            lst = await c.get("/api/v1/skills", headers=_h(tenant))
            row = next(s for s in lst.json()["items"] if s["id"] == skill_id)
            assert row["version"] == "0.2.0"
            assert "delivery note" in row["instructions"]


async def test_republishing_the_same_semver_is_rejected() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            skill_id = (
                await c.post("/api/v1/skills", json=_body(), headers=_h(tenant))
            ).json()["id"]
            r = await c.post(
                f"/api/v1/skills/{skill_id}/versions",
                json={"semver": "0.1.0", "instructions": "same version again"},
                headers=_h(tenant),
            )
            assert r.status_code == 409, r.text


async def test_versioning_another_tenants_skill_is_a_404() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            skill_id = (
                await c.post("/api/v1/skills", json=_body(), headers=_h(tenant_a))
            ).json()["id"]
            r = await c.post(
                f"/api/v1/skills/{skill_id}/versions",
                json={"semver": "0.2.0", "instructions": "hijack"},
                headers=_h(tenant_b),
            )
            assert r.status_code == 404


# ---------------------------------------------------------------- assignments

async def _agent(app_session: Any, tenant: uuid.UUID) -> uuid.UUID:
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Ops", frame={})
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
        return agent.id


async def test_assignments_can_be_listed_and_removed(app_session: Any) -> None:
    tenant = uuid.uuid4()
    agent_id = await _agent(app_session, tenant)
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            skill = (
                await c.post(
                    "/api/v1/skills", json=_body(tools=[], guardrails=[]), headers=_h(tenant)
                )
            ).json()

            empty = await c.get(f"/api/v1/agents/{agent_id}/skills", headers=_h(tenant))
            assert empty.status_code == 200, empty.text
            assert empty.json() == []

            a = await c.post(
                f"/api/v1/agents/{agent_id}/skills",
                json={"skillVersionId": skill["currentVersionId"]},
                headers=_h(tenant),
            )
            assert a.status_code == 201, a.text

            listed = await c.get(f"/api/v1/agents/{agent_id}/skills", headers=_h(tenant))
            rows = listed.json()
            assert len(rows) == 1
            assert rows[0]["skillName"] == "Invoice check"
            assert rows[0]["enabled"] is True

            d = await c.delete(
                f"/api/v1/agents/{agent_id}/skills/{rows[0]['id']}", headers=_h(tenant)
            )
            assert d.status_code == 204, d.text

            after = await c.get(f"/api/v1/agents/{agent_id}/skills", headers=_h(tenant))
            assert after.json() == []


async def test_another_tenant_cannot_read_or_remove_an_assignment(app_session: Any) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    agent_id = await _agent(app_session, tenant_a)
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            skill = (
                await c.post(
                    "/api/v1/skills", json=_body(tools=[], guardrails=[]), headers=_h(tenant_a)
                )
            ).json()
            await c.post(
                f"/api/v1/agents/{agent_id}/skills",
                json={"skillVersionId": skill["currentVersionId"]},
                headers=_h(tenant_a),
            )
            rows = (
                await c.get(f"/api/v1/agents/{agent_id}/skills", headers=_h(tenant_a))
            ).json()

            assert (
                await c.get(f"/api/v1/agents/{agent_id}/skills", headers=_h(tenant_b))
            ).status_code == 404
            assert (
                await c.delete(
                    f"/api/v1/agents/{agent_id}/skills/{rows[0]['id']}", headers=_h(tenant_b)
                )
            ).status_code == 404


async def test_removing_an_assignment_requires_admin(app_session: Any) -> None:
    tenant = uuid.uuid4()
    agent_id = await _agent(app_session, tenant)
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            skill = (
                await c.post(
                    "/api/v1/skills", json=_body(tools=[], guardrails=[]), headers=_h(tenant)
                )
            ).json()
            await c.post(
                f"/api/v1/agents/{agent_id}/skills",
                json={"skillVersionId": skill["currentVersionId"]},
                headers=_h(tenant),
            )
            rows = (
                await c.get(f"/api/v1/agents/{agent_id}/skills", headers=_h(tenant))
            ).json()
            r = await c.delete(
                f"/api/v1/agents/{agent_id}/skills/{rows[0]['id']}", headers=_h(tenant, "member")
            )
            assert r.status_code == 403


async def test_an_authored_skill_is_actually_loadable_at_runtime(app_session: Any) -> None:
    """The point of authoring: a skill created through the API must be a real
    skill the runtime can load, not just a row that renders in a list."""
    from oc8.skills.runtime import load_assigned_skills

    tenant = uuid.uuid4()
    agent_id = await _agent(app_session, tenant)
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            skill = (
                await c.post(
                    "/api/v1/skills", json=_body(tools=[], guardrails=[]), headers=_h(tenant)
                )
            ).json()
            await c.post(
                f"/api/v1/agents/{agent_id}/skills",
                json={"skillVersionId": skill["currentVersionId"]},
                headers=_h(tenant),
            )

    async with app_session(tenant) as db:
        agent = await db.get(m.Agent, agent_id)
        loaded = await load_assigned_skills(db, agent=agent, tenant_id=tenant)
    assert len(loaded) == 1
    assert "purchase order" in loaded[0].definition.instruction


async def test_an_assignment_cannot_be_removed_via_another_agents_url(app_session: Any) -> None:
    """RLS separates tenants but NOT two agents of the same tenant: without the
    explicit agent_id check, agent B's assignment would be deletable through
    agent A's URL. Found by mutation testing, not by review."""
    tenant = uuid.uuid4()
    agent_a = await _agent(app_session, tenant)
    agent_b = await _agent(app_session, tenant)
    app = create_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            skill = (
                await c.post(
                    "/api/v1/skills", json=_body(tools=[], guardrails=[]), headers=_h(tenant)
                )
            ).json()
            await c.post(
                f"/api/v1/agents/{agent_b}/skills",
                json={"skillVersionId": skill["currentVersionId"]},
                headers=_h(tenant),
            )
            rows = (
                await c.get(f"/api/v1/agents/{agent_b}/skills", headers=_h(tenant))
            ).json()
            assert len(rows) == 1

            wrong = await c.delete(
                f"/api/v1/agents/{agent_a}/skills/{rows[0]['id']}", headers=_h(tenant)
            )
            assert wrong.status_code == 404

            still = (
                await c.get(f"/api/v1/agents/{agent_b}/skills", headers=_h(tenant))
            ).json()
            assert len(still) == 1, "agent B's assignment must survive"
