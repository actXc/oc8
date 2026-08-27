"""Regression for a defect an adversarial review found in the department-scoped
-agent-authority slice (migration 0048): `perm(AGENT, VIEW)`/`perm(DEPARTMENT,
VIEW)` graduated into `DELEGATABLE_PERMISSIONS` on the claim that "the route
body still filters by `scope.viewable`, which a role assignment never widens."

That claim did not hold. `agents.py`/`departments.py` computed `tenant_wide`
as `perm(...) in authority.tenant_wide` -- true for ANY holder of the string,
including a tenant-defined role an admin just composed in the role builder.
`agents.repo.visible_agents`/`visible_agent` (and the department equivalents)
skip the `scope.viewable` filter ENTIRELY when `tenant_wide` is true, so a
member holding a custom role with only `agent:view`+`department:view` --
and NO seat in ANY department -- got full-tenant, all-departments read access:
every agent's narrowing and effective tool policy, every department's task
board and tool frame, automatically including departments created afterward.

This is the exact "unrestricted" property the codebase reserves for
`approval:view_any` (`NEVER_DELEGATABLE`, never reachable through a
tenant-defined role) -- `approvals/repo.py::visible_approvals` only skips its
own department filter on `scope.is_unrestricted`, never on holding
`approval:view` itself. `agents.py`/`departments.py` now match that pattern
via `authz.authority.tenant_wide_read`, which admits the bypass only for the
token floor or an explicitly assigned `builtin=True` role -- never for an
assigned, tenant-defined one.

This file pins BOTH halves: the bypass is closed for the custom-role holder,
and it stays open for the built-in tenant-wide roles that relied on it before
this permission was ever delegatable (a regression the naive "just check
`scope.is_unrestricted` instead" fix would have introduced -- `dept_manager`
and `operator` hold neither `approval:view_any` nor `decides_everywhere`, so
that fix would have silently blinded them on `/agents` and `/departments`).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.authz.permissions import AGENT, DEPARTMENT, VIEW, perm
from oc8.authz.scope import subject_uuid_for
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID, subject: str, role: str = "member") -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=role)
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


@dataclass
class _Office:
    tenant: uuid.UUID
    sales: uuid.UUID
    engineering: uuid.UUID
    sales_agent: uuid.UUID
    engineering_agent: uuid.UUID
    #: Holds a TENANT-DEFINED role granting only agent:view + department:view.
    #: No seat, in Sales, Engineering, or anywhere else.
    seatless_reader: str = "seatless-reader"
    #: Assigned the built-in `dept_manager` ROW explicitly (source == "assigned",
    #: role.builtin == True) -- must keep today's flat tenant-wide read.
    assigned_dept_manager: str = "assigned-dept-manager"


async def _office(app_session: AppSessionFactory) -> _Office:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
        engineering = m.Department(tenant_id=tenant, name="Entwicklung", frame={})
        db.add_all([sales, engineering])
        await db.flush()

        sales_agent = m.Agent(
            tenant_id=tenant,
            department_id=sales.id,
            name="Nora",
            status="stopped",
            narrowing={},
            definition={},
            presentation={},
        )
        engineering_agent = m.Agent(
            tenant_id=tenant,
            department_id=engineering.id,
            name="Theo",
            status="stopped",
            narrowing={},
            definition={},
            presentation={},
        )
        db.add_all([sales_agent, engineering_agent])
        await db.flush()

        office = _Office(
            tenant=tenant,
            sales=sales.id,
            engineering=engineering.id,
            sales_agent=sales_agent.id,
            engineering_agent=engineering_agent.id,
        )

        # The tenant-defined role: a real admin would compose exactly this in
        # the role builder today, since `validated_permissions` accepts both
        # strings (`test_agent_view_and_department_view_are_now_delegatable`).
        reader_role = m.Role(tenant_id=tenant, name="Reader", kind="human", builtin=False)
        db.add(reader_role)
        await db.flush()
        db.add_all(
            [
                m.RolePermission(
                    tenant_id=tenant, role_id=reader_role.id, permission=perm(AGENT, VIEW)
                ),
                m.RolePermission(
                    tenant_id=tenant, role_id=reader_role.id, permission=perm(DEPARTMENT, VIEW)
                ),
            ]
        )
        db.add(
            m.OrgMember(
                tenant_id=tenant,
                subject=office.seatless_reader,
                subject_uuid=subject_uuid_for(office.seatless_reader),
                role_id=reader_role.id,
            )
        )

        # A built-in row (`dept_manager`), assigned explicitly rather than read
        # off the token -- exercises the OTHER branch of `tenant_wide_read`
        # (`source == "assigned"`, `role.builtin == True`). `Role.name` here
        # only has to be a name `permissions_for` recognises -- `_role_and_
        # permissions` reads its grants from `BUILTIN_ROLE_PERMISSIONS[name]`
        # by code, not from `role_permission` rows, for any `builtin=True` row.
        dept_manager_role = m.Role(
            tenant_id=tenant, name="dept_manager", kind="human", builtin=True
        )
        db.add(dept_manager_role)
        await db.flush()
        db.add(
            m.OrgMember(
                tenant_id=tenant,
                subject=office.assigned_dept_manager,
                subject_uuid=subject_uuid_for(office.assigned_dept_manager),
                role_id=dept_manager_role.id,
            )
        )
        await db.flush()
    return office


async def test_a_seatless_tenant_defined_role_holder_cannot_read_past_scope_viewable(
    app_session: AppSessionFactory,
) -> None:
    """The exploit an adversarial review reproduced: `agent:view` +
    `department:view`, held tenant-wide via a CUSTOM role, with no seat
    anywhere. Every one of the five graduated GET routes must come back
    empty/404 -- byte-identical to a caller with no grant at all, because
    `scope.viewable` is empty and `tenant_wide_read` must not admit this
    holder's bypass."""
    office = await _office(app_session)
    async with _http() as http:
        h = _headers(office.tenant, office.seatless_reader)

        # `GET /agents` and `GET /departments` return `Page[...]` (Task 6/7 of
        # the Design System Consistency plan), not a bare list.
        agents = await http.get("/api/v1/agents", headers=h)
        assert agents.status_code == 200, agents.text
        assert agents.json()["items"] == []

        for agent_id in (office.sales_agent, office.engineering_agent):
            resp = await http.get(f"/api/v1/agents/{agent_id}", headers=h)
            assert resp.status_code == 404, resp.text

        departments = await http.get("/api/v1/departments", headers=h)
        assert departments.status_code == 200, departments.text
        assert departments.json()["items"] == []

        for dept_id in (office.sales, office.engineering):
            resp = await http.get(f"/api/v1/departments/{dept_id}", headers=h)
            assert resp.status_code == 404, resp.text

            tools = await http.get(f"/api/v1/departments/{dept_id}/tools", headers=h)
            assert tools.status_code == 404, tools.text

            dept_agents = await http.get(f"/api/v1/departments/{dept_id}/agents", headers=h)
            assert dept_agents.status_code == 404, dept_agents.text

            board = await http.get(f"/api/v1/departments/{dept_id}/board", headers=h)
            assert board.status_code == 404, board.text


async def test_an_assigned_builtin_tenant_wide_role_keeps_flat_read(
    app_session: AppSessionFactory,
) -> None:
    """The other half of the fix: a `dept_manager` row explicitly ASSIGNED
    (`org_member.role_id`, not the token floor) must still see every
    department and every agent, exactly as it did before `agent:view`/
    `department:view` became delegatable. A fix that keyed the bypass off
    `scope.is_unrestricted` instead of `tenant_wide_read` would blind this
    caller -- `dept_manager` holds neither `approval:view_any` nor
    `all_departments`."""
    office = await _office(app_session)
    async with _http() as http:
        h = _headers(office.tenant, office.assigned_dept_manager)

        agents = await http.get("/api/v1/agents", headers=h)
        assert agents.status_code == 200, agents.text
        agent_ids = {a["id"] for a in agents.json()["items"]}
        assert agent_ids == {str(office.sales_agent), str(office.engineering_agent)}

        departments = await http.get("/api/v1/departments", headers=h)
        assert departments.status_code == 200, departments.text
        dept_ids = {d["id"] for d in departments.json()["items"]}
        assert dept_ids == {str(office.sales), str(office.engineering)}

        for dept_id in (office.sales, office.engineering):
            resp = await http.get(f"/api/v1/departments/{dept_id}", headers=h)
            assert resp.status_code == 200, resp.text
