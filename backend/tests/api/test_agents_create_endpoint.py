"""`POST /agents` accepting `runtimePluginId` at hire time.

Task 3 of the agent-runtime-selection slice: hiring calls the SAME
`assign_runtime` helper `PUT /agents/{id}/runtime` uses (Task 2), so the two
routes cannot drift on what counts as a valid runtime. The parity test below
pins that directly -- a bad plugin id must fail identically on both routes.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.capas.lifecycle import enable_plugin
from oc8.capas.service import install_plugin
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _token(tenant: uuid.UUID) -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")


def _h(tenant: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(tenant)}"}


async def _install_stub_runtime(
    db: AsyncSession, tenant: uuid.UUID, *, capabilities: list[str] | None = None
) -> uuid.UUID:
    # Unique semver suffix per call -- see the identical note in
    # tests/runtime/test_registry.py and tests/api/test_agents_runtime.py.
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


async def test_create_agent_with_valid_runtime_plugin_id_sets_ref(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        plugin_id = await _install_stub_runtime(db, tenant)
        dept = m.Department(tenant_id=tenant, name="Eng", frame={})
        db.add(dept)
        await db.flush()
        dept_id = dept.id

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(
                "/api/v1/agents",
                json={
                    "name": "Runtime Test Agent",
                    "departmentId": str(dept_id),
                    "roleTitle": "Tester",
                    "mission": "test",
                    "runtimePluginId": str(plugin_id),
                },
                headers=_h(tenant),
            )
            assert resp.status_code == 201, resp.text
            assert resp.json()["runtimeRef"] == str(plugin_id)


async def test_create_agent_without_runtime_leaves_ref_null(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Eng", frame={})
        db.add(dept)
        await db.flush()
        dept_id = dept.id

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(
                "/api/v1/agents",
                json={"name": "Plain Agent", "departmentId": str(dept_id)},
                headers=_h(tenant),
            )
            assert resp.status_code == 201, resp.text
            assert resp.json()["runtimeRef"] is None


async def test_create_agent_with_null_runtime_leaves_ref_null(
    app_session: AppSessionFactory,
) -> None:
    """An explicit null must behave exactly like an omitted field."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Eng", frame={})
        db.add(dept)
        await db.flush()
        dept_id = dept.id

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(
                "/api/v1/agents",
                json={
                    "name": "Plain Agent 2",
                    "departmentId": str(dept_id),
                    "runtimePluginId": None,
                },
                headers=_h(tenant),
            )
            assert resp.status_code == 201, resp.text
            assert resp.json()["runtimeRef"] is None


async def test_creating_an_agent_without_a_runtime_audits_nothing_about_runtimes(
    app_session: AppSessionFactory,
) -> None:
    """Nothing was set, so nothing was cleared.

    The create path shares `assign_runtime` with `PUT .../runtime`, and that
    helper audits unconditionally. Passing it None on every hire would write an
    `agent.runtime.cleared` event describing a deliberate operator action that
    nobody performed -- once per agent, for ever, in the log an audit reads
    first.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Eng", frame={})
        db.add(dept)
        await db.flush()
        dept_id = dept.id

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(
                "/api/v1/agents",
                json={"name": "Unaudited Agent", "departmentId": str(dept_id)},
                headers=_h(tenant),
            )
            assert resp.status_code == 201, resp.text
            agent_id = resp.json()["id"]

    async with app_session(tenant) as db:
        rows = (
            (
                await db.execute(
                    sa.select(m.AuditEvent.action).where(
                        m.AuditEvent.tenant_id == tenant,
                        m.AuditEvent.action.like("agent.runtime.%"),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert rows == [], f"hiring {agent_id} wrote runtime audit events: {rows}"


async def test_clearing_a_runtime_through_put_still_audits(
    app_session: AppSessionFactory,
) -> None:
    """The other half of the rule above: an explicit clear IS an operator action."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Eng", frame={})
        db.add(dept)
        await db.flush()
        dept_id = dept.id

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            created = await client.post(
                "/api/v1/agents",
                json={"name": "Cleared Agent", "departmentId": str(dept_id)},
                headers=_h(tenant),
            )
            assert created.status_code == 201, created.text
            agent_id = created.json()["id"]

            cleared = await client.put(
                f"/api/v1/agents/{agent_id}/runtime",
                json={"runtimePluginId": None},
                headers=_h(tenant),
            )
            assert cleared.status_code == 200, cleared.text

    async with app_session(tenant) as db:
        rows = (
            (
                await db.execute(
                    sa.select(m.AuditEvent.action).where(
                        m.AuditEvent.tenant_id == tenant,
                        m.AuditEvent.action.like("agent.runtime.%"),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert rows == ["agent.runtime.cleared"], rows


async def test_create_agent_with_bad_runtime_matches_put_status(
    app_session: AppSessionFactory,
) -> None:
    """`POST /agents` and `PUT /agents/{id}/runtime` must reject the same
    unknown plugin id with the SAME status -- both routes run through the
    same `assign_runtime` helper, so they cannot drift."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Eng", frame={})
        db.add(dept)
        await db.flush()
        existing_agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Existing")
        db.add(existing_agent)
        await db.flush()
        dept_id, existing_agent_id = dept.id, existing_agent.id

    bad_id = str(uuid.uuid4())
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            post_resp = await client.post(
                "/api/v1/agents",
                json={
                    "name": "Bad Runtime Agent",
                    "departmentId": str(dept_id),
                    "runtimePluginId": bad_id,
                },
                headers=_h(tenant),
            )
            put_resp = await client.put(
                f"/api/v1/agents/{existing_agent_id}/runtime",
                json={"runtimePluginId": bad_id},
                headers=_h(tenant),
            )

    assert post_resp.status_code == put_resp.status_code == 404, (
        post_resp.text,
        put_resp.text,
    )

    # A hire request that fails runtime validation must not leave a partial
    # agent row behind -- there is no second commit here, so the failed
    # `assign_runtime` call has to roll back the whole request.
    async with app_session(tenant) as db:
        query = m.Agent.__table__.select().where(m.Agent.name == "Bad Runtime Agent")
        rows = (await db.execute(query)).fetchall()
        assert rows == []
