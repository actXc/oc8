"""`AgentDetailDTO.narrowing_tools` -- the agent's own stored narrowing,
returned verbatim alongside `effective_tools` (role_rights ∩ frame ∩
narrowing) and `department_frame_tools`.

Exists because the Configuration and Guardrails tabs both resave whatever
fields they don't themselves edit by reading from *some* prior source --
and until this field existed, that source was `effective_tools`. A dangling
or wrong-kind `agent.role_id` (see `pdp.agent_tool_rights`'s own docstring)
makes `agent_tool_rights` return no rights at all, which zeroes read/write/
send in `effective_tools` even though the agent's actual narrowing and the
department frame are both untouched -- and re-persisting that zeroed value
bakes a transient role problem into the agent's stored narrowing forever.
`narrowing_tools` gives the frontend a source immune to that dip.
"""

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


async def _seed_agent_with_narrowing(
    app_session: AppSessionFactory, tenant: uuid.UUID, *, role_id: uuid.UUID | None = None
) -> uuid.UUID:
    async with app_session(tenant) as db:
        dept = m.Department(
            tenant_id=tenant,
            name="General",
            frame={
                "tools": {
                    "odoo": {"enabled": True, "read": True, "modify": True, "modify": True}
                }
            },
        )
        db.add(dept)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant,
            department_id=dept.id,
            name="Lennart",
            role_id=role_id,
            narrowing={
                "tools": {
                    "odoo": {
                        "enabled": True,
                        "read": True,
                        "modify": True,
                        "connection_id": None,
                    }
                }
            },
        )
        db.add(agent)
        await db.flush()
        return agent.id


async def test_narrowing_tools_reflects_the_agents_own_stored_narrowing(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent_id = await _seed_agent_with_narrowing(app_session, tenant)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get(f"/api/v1/agents/{agent_id}", headers=_headers(tenant))
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["narrowingTools"]["odoo"] == {
                "enabled": True,
                "read": True,
                "modify": True,
                "approvalEur": None,
                "approvalActions": [],
                "only": None,
                "connectionId": None,
            }


async def test_narrowing_tools_stays_clean_when_a_dangling_role_degrades_effective_tools(
    app_session: AppSessionFactory,
) -> None:
    """The regression this field exists to fix: a role reference that no
    longer resolves must not appear to have touched the agent's own stored
    narrowing, even though it zeroes `effectiveTools`."""
    tenant = uuid.uuid4()
    dangling_role_id = uuid.uuid4()
    agent_id = await _seed_agent_with_narrowing(app_session, tenant, role_id=dangling_role_id)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get(f"/api/v1/agents/{agent_id}", headers=_headers(tenant))
            assert r.status_code == 200, r.text
            body = r.json()

            # The dangling role_id really does degrade effectiveTools -- this
            # asserts the premise, not just the fix.
            assert body["effectiveTools"]["odoo"]["read"] is False
            assert body["effectiveTools"]["odoo"]["write"] is False
            assert body["effectiveTools"]["odoo"]["send"] is False

            # narrowingTools and departmentFrameTools are both untouched by
            # the role problem -- this is what the frontend must resave from.
            assert body["narrowingTools"]["odoo"]["read"] is True
            assert body["narrowingTools"]["odoo"]["write"] is True
            assert body["narrowingTools"]["odoo"]["send"] is True
            assert body["departmentFrameTools"]["odoo"]["read"] is True
            assert body["departmentFrameTools"]["odoo"]["write"] is True
            assert body["departmentFrameTools"]["odoo"]["send"] is True


async def test_narrowing_tools_is_empty_for_an_agent_with_no_narrowing_set(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="General", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Fresh")
        db.add(agent)
        await db.flush()
        agent_id = agent.id
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get(f"/api/v1/agents/{agent_id}", headers=_headers(tenant))
            assert r.status_code == 200, r.text
            assert r.json()["narrowingTools"] == {}
