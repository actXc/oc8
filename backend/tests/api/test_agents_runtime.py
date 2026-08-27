from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.capas.lifecycle import enable_plugin
from oc8.capas.service import install_plugin
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _token(tenant: uuid.UUID) -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")


async def _install_stub_runtime(
    db: AsyncSession, tenant: uuid.UUID, *, capabilities: list[str] | None = None
) -> uuid.UUID:
    # Unique semver suffix per call — ACME_TENANT_ID is shared across the
    # whole suite and this helper is called by multiple tests in this file;
    # see the identical note in tests/runtime/test_registry.py.
    version = await install_plugin(
        db,
        tenant_id=tenant,
        manifest_data={
            "name": "oc8.echo-runtime-stub",
            "version": f"1.0.0+{uuid.uuid4().hex[:8]}",
            "type": "runtime_adapter",
            "trust": "community",
            "capabilities": capabilities if capabilities is not None else ["streaming"],
        },
    )
    await enable_plugin(db, tenant_id=tenant, capa_id=version.capa_id, granted_permissions=[])
    return version.capa_id


def _h(tenant: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(tenant)}"}


async def _seed(db: AsyncSession, tenant: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    plugin_id = await _install_stub_runtime(db, tenant)
    agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="A")
    db.add(agent)
    await db.flush()
    return agent.id, plugin_id


async def test_assign_runtime_succeeds_for_plain_agent(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        plugin_id = await _install_stub_runtime(db, tenant)
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="A")
        db.add(agent)
        await db.flush()
        agent_id = agent.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.put(
                f"/api/v1/agents/{agent_id}/runtime",
                json={"runtimePluginId": str(plugin_id)},
                headers={"Authorization": f"Bearer {_token(tenant)}"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["runtimeRef"] == str(plugin_id)


async def test_assign_runtime_rejected_for_unknown_plugin(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="A")
        db.add(agent)
        await db.flush()
        agent_id = agent.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.put(
                f"/api/v1/agents/{agent_id}/runtime",
                json={"runtimePluginId": str(uuid.uuid4())},
                headers={"Authorization": f"Bearer {_token(tenant)}"},
            )
            assert r.status_code == 404, r.text


async def test_assign_runtime_null_clears_to_default(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        plugin_id = await _install_stub_runtime(db, tenant)
        agent = m.Agent(
            tenant_id=tenant, department_id=uuid.uuid4(), name="A", runtime_ref=str(plugin_id)
        )
        db.add(agent)
        await db.flush()
        agent_id = agent.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.put(
                f"/api/v1/agents/{agent_id}/runtime",
                json={"runtimePluginId": None},
                headers={"Authorization": f"Bearer {_token(tenant)}"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["runtimeRef"] is None


async def test_assign_skill_rejected_when_current_runtime_lacks_skills_capability(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        plugin_id = await _install_stub_runtime(db, tenant, capabilities=["checkpoints"])
        dept = m.Department(tenant_id=tenant, name="Eng", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant, department_id=dept.id, name="A", runtime_ref=str(plugin_id)
        )
        db.add(agent)
        skill = m.Skill(tenant_id=tenant, name="do-thing", category="general")
        db.add(skill)
        await db.flush()
        version = m.SkillVersion(
            tenant_id=tenant,
            skill_id=skill.id,
            semver="1.0.0",
            definition={"requires": {}},
            artifact_hash=b"test",
        )
        db.add(version)
        await db.flush()
        agent_id, version_id = agent.id, version.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                f"/api/v1/agents/{agent_id}/skills",
                json={"skillVersionId": str(version_id)},
                headers={"Authorization": f"Bearer {_token(tenant)}"},
            )
            assert r.status_code == 422, r.text
            body = r.json()["detail"]
            assert body["error"] == "runtime_capability_violation"
            assert any(m["missingCapability"] == "skills" for m in body["missing"])


async def test_a_misspelled_field_is_refused_instead_of_clearing_the_runtime(
    app_session: AppSessionFactory,
) -> None:
    """The field is `runtimePluginId`. Anything else used to be ignored, and
    since an absent id MEANS "clear", the request then did the opposite of what
    it asked: it unassigned the runtime and audited it as deliberate.

    Cost a live debugging round -- the agent silently fell back to the default
    runtime and nothing anywhere said why.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, plugin_id = await _seed(db, tenant)
        await db.commit()

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            ok = await c.put(
                f"/api/v1/agents/{agent_id}/runtime",
                json={"runtimePluginId": str(plugin_id)},
                headers=_h(tenant),
            )
            assert ok.status_code == 200, ok.text

            typo = await c.put(
                f"/api/v1/agents/{agent_id}/runtime",
                json={"runtimeRef": str(plugin_id)},
                headers=_h(tenant),
            )
            assert typo.status_code == 422, typo.text

            empty = await c.put(f"/api/v1/agents/{agent_id}/runtime", json={}, headers=_h(tenant))
            assert empty.status_code == 422, "an empty body must not mean 'clear'"

    async with app_session(tenant) as db:
        agent = await db.get(m.Agent, agent_id)
        assert agent is not None
        assert agent.runtime_ref == str(plugin_id), "the assignment must have survived"


async def test_assign_runtime_accepts_the_builtin_sentinels(
    app_session: AppSessionFactory,
) -> None:
    """The two built-in runtimes are now independently selectable, not just a
    tenant-wide `agent_isolation` toggle (see GET /runtimes and
    oc8.runtime.registry.assign_runtime) -- assigning either sentinel by
    string, with no Capa row behind it, must succeed exactly like assigning a
    real plugin id does."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="A")
        db.add(agent)
        await db.flush()
        agent_id = agent.id

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            isolated = await c.put(
                f"/api/v1/agents/{agent_id}/runtime",
                json={"runtimePluginId": "builtin:isolated"},
                headers=_h(tenant),
            )
            assert isolated.status_code == 200, isolated.text
            assert isolated.json()["runtimeRef"] == "builtin:isolated"

            in_process = await c.put(
                f"/api/v1/agents/{agent_id}/runtime",
                json={"runtimePluginId": "builtin:in-process"},
                headers=_h(tenant),
            )
            assert in_process.status_code == 200, in_process.text
            assert in_process.json()["runtimeRef"] == "builtin:in-process"


async def test_clearing_still_works_when_asked_for_explicitly(
    app_session: AppSessionFactory,
) -> None:
    """Clearing is a real feature -- it just has to be said out loud now."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, plugin_id = await _seed(db, tenant)
        await db.commit()

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            await c.put(
                f"/api/v1/agents/{agent_id}/runtime",
                json={"runtimePluginId": str(plugin_id)},
                headers=_h(tenant),
            )
            cleared = await c.put(
                f"/api/v1/agents/{agent_id}/runtime",
                json={"runtimePluginId": None},
                headers=_h(tenant),
            )
            assert cleared.status_code == 200, cleared.text

    async with app_session(tenant) as db:
        agent = await db.get(m.Agent, agent_id)
        assert agent is not None
        assert agent.runtime_ref is None
