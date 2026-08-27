"""READ graduates automatically for any live seat -- no toggle, no admin action,
just holding a seat somewhere (decision 2). Seven routes move from a flat
tenant-wide `require_permission` gate to `require_departmental`, backed by the
new `agents.repo` / `departments.repo` funnel: `GET /agents`,
`GET /agents/{id}`, `GET /departments`, `GET /departments/{id}`,
`GET /departments/{id}/tools`, `GET /departments/{id}/agents`,
`GET /departments/{id}/board`.

A Sales-seated `member`-role caller -- token role `member`, which holds the
empty tenant-wide set, so every assertion below is the SEAT doing the work and
nothing else -- gets 200s scoped to Sales and is refused Engineering exactly as
the route already refuses a resource that does not exist: `visible_agent` /
`visible_department` return `None` for BOTH reasons, on purpose, so a caller
who has plainly seen Sales cannot use the department board or agent list to
learn that Engineering exists at all.
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
from oc8.authz.permissions import SEAT_VIEWER
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


def _subject_uuid(subject: str) -> uuid.UUID:
    try:
        return uuid.UUID(subject)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"oc8:subject:{subject}")


@dataclass
class _Office:
    tenant: uuid.UUID
    sales: uuid.UUID
    engineering: uuid.UUID
    sales_agent: uuid.UUID
    engineering_agent: uuid.UUID
    #: A live `dept_viewer` seat in `sales` ONLY. No tenant-wide grant.
    seated_subject: str = "sales-seat"


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

        member = m.OrgMember(
            tenant_id=tenant,
            subject=office.seated_subject,
            subject_uuid=_subject_uuid(office.seated_subject),
        )
        db.add(member)
        await db.flush()
        db.add(
            m.OrgMemberDepartment(
                tenant_id=tenant,
                member_id=member.id,
                department_id=sales.id,
                seat_role=SEAT_VIEWER,
            )
        )
        await db.flush()
    return office


# ---------------------------------------------------------------------- test 15


async def test_a_sales_seated_caller_is_scoped_to_sales_on_all_seven_graduated_reads(
    app_session: AppSessionFactory,
) -> None:
    office = await _office(app_session)
    async with _http() as http:
        h = _headers(office.tenant, office.seated_subject)

        # `GET /agents` and `GET /departments` return `Page[...]` (Task 6/7 of
        # the Design System Consistency plan), not a bare list.
        agents = await http.get("/api/v1/agents", headers=h)
        assert agents.status_code == 200, agents.text
        agent_ids = {a["id"] for a in agents.json()["items"]}
        assert str(office.sales_agent) in agent_ids
        assert str(office.engineering_agent) not in agent_ids

        # `?departmentId=` for a department outside scope intersects down to
        # `[]`, never 403 -- it is a filter over what the caller may already
        # see, not a second door.
        foreign_filtered = await http.get(
            "/api/v1/agents", params={"departmentId": str(office.engineering)}, headers=h
        )
        assert foreign_filtered.status_code == 200, foreign_filtered.text
        assert foreign_filtered.json()["items"] == []

        own_agent = await http.get(f"/api/v1/agents/{office.sales_agent}", headers=h)
        assert own_agent.status_code == 200, own_agent.text

        foreign_agent = await http.get(f"/api/v1/agents/{office.engineering_agent}", headers=h)
        assert foreign_agent.status_code == 404, foreign_agent.text
        assert "agent not found" in foreign_agent.text

        departments = await http.get("/api/v1/departments", headers=h)
        assert departments.status_code == 200, departments.text
        dept_ids = {d["id"] for d in departments.json()["items"]}
        assert str(office.sales) in dept_ids
        assert str(office.engineering) not in dept_ids

        own_dept = await http.get(f"/api/v1/departments/{office.sales}", headers=h)
        assert own_dept.status_code == 200, own_dept.text

        foreign_dept = await http.get(f"/api/v1/departments/{office.engineering}", headers=h)
        assert foreign_dept.status_code == 404, foreign_dept.text
        assert "department not found" in foreign_dept.text

        own_tools = await http.get(f"/api/v1/departments/{office.sales}/tools", headers=h)
        assert own_tools.status_code == 200, own_tools.text

        foreign_tools = await http.get(f"/api/v1/departments/{office.engineering}/tools", headers=h)
        assert foreign_tools.status_code == 404, foreign_tools.text

        own_dept_agents = await http.get(f"/api/v1/departments/{office.sales}/agents", headers=h)
        assert own_dept_agents.status_code == 200, own_dept_agents.text
        assert {a["id"] for a in own_dept_agents.json()} == {str(office.sales_agent)}

        own_board = await http.get(f"/api/v1/departments/{office.sales}/board", headers=h)
        assert own_board.status_code == 200, own_board.text


# ---------------------------------------------------------------------- test 16


async def test_department_agents_and_board_404_consistently_on_foreign_or_bogus_id(
    app_session: AppSessionFactory,
) -> None:
    """Before this design, a `dept_id` outside scope returned 200 with an empty
    list on both of these -- indistinguishable from "this department exists and
    has nothing in it". `visible_department`'s None-on-either-reason contract
    makes a genuinely nonexistent id and a real, foreign department answer with
    the SAME 404, so neither route can be used to learn which one it was."""
    office = await _office(app_session)
    bogus = uuid.uuid4()
    async with _http() as http:
        h = _headers(office.tenant, office.seated_subject)

        for dept_id in (office.engineering, bogus):
            agents_resp = await http.get(f"/api/v1/departments/{dept_id}/agents", headers=h)
            assert agents_resp.status_code == 404, (
                f"{dept_id}: expected 404, got {agents_resp.status_code} {agents_resp.text}"
            )

            board_resp = await http.get(f"/api/v1/departments/{dept_id}/board", headers=h)
            assert board_resp.status_code == 404, (
                f"{dept_id}: expected 404, got {board_resp.status_code} {board_resp.text}"
            )
