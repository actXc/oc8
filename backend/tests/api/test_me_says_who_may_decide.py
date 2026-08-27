"""`/me` sent one flag where the backend keeps two, and the screen paid for it.

`authz/scope.py` carries `_unrestricted` and `_decide_everywhere` separately and
documents at length why they must not become one: an `auditor` holds
`approval:view_any` and deliberately not `approval:decide_any`, so he sees every
department and may sign off in none. The whole value of the role is that its
account cannot have caused what it is auditing.

`MeDTO` carried only `viewsAllDepartments`, so `workspace.tsx::mayActOn` returned
true for him and drew Reject and Approve on every row -- and
`require_departmental(approval:decide)` 403'd every click. The answer was in the
scope and simply never left the process.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.authz.permissions import AUDITOR, MEMBER_ROLE, ORG_ADMIN, SEAT_APPROVER, SEAT_VIEWER
from oc8.authz.scope import subject_uuid_for
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID, subject: str, role: str) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=role)
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def _seat(
    app_session: AppSessionFactory, tenant: uuid.UUID, subject: str, seat_role: str
) -> uuid.UUID:
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb")
        db.add(dept)
        await db.flush()
        member = m.OrgMember(
            tenant_id=tenant,
            subject=subject,
            subject_uuid=subject_uuid_for(subject),
            display_name=subject,
        )
        db.add(member)
        await db.flush()
        db.add(
            m.OrgMemberDepartment(
                tenant_id=tenant,
                member_id=member.id,
                department_id=dept.id,
                seat_role=seat_role,
            )
        )
        return dept.id


async def test_an_auditor_sees_everywhere_and_says_he_decides_nowhere(
    app_session: AppSessionFactory,
) -> None:
    """The two flags disagree for exactly one built-in role, and that is the
    whole reason there are two. The 403 is asserted beside them so the screen's
    claim and the backend's answer are pinned to each other in one test.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Entwicklung")
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Ada")
        db.add(agent)
        await db.flush()
        held = m.ApprovalRequest(
            tenant_id=tenant,
            agent_id=agent.id,
            department_id=dept.id,
            action_type="tool_send",
            status="pending",
            title="Produktionszugang",
            detail="",
        )
        db.add(held)
        await db.flush()
        held_id = held.id

    async with _http() as http:
        me = (await http.get("/api/v1/me", headers=_headers(tenant, "pruefer", AUDITOR))).json()
        assert me["viewsAllDepartments"] is True
        assert me["decidesAllDepartments"] is False, (
            "the screen reads this to decide whether to draw Approve; with only "
            "viewsAllDepartments on the wire it offered an auditor a button that "
            "403s on every click"
        )
        listed = await http.get("/api/v1/approvals", headers=_headers(tenant, "pruefer", AUDITOR))
        assert listed.status_code == 200
        assert [r["id"] for r in listed.json()] == [str(held_id)]

        refused = await http.post(
            f"/api/v1/approvals/{held_id}/decision",
            json={"decision": "approve"},
            headers=_headers(tenant, "pruefer", AUDITOR),
        )
        assert refused.status_code == 403


async def test_an_org_admin_says_he_decides_everywhere(app_session: AppSessionFactory) -> None:
    """The guard against fixing the auditor by hiding the button from everybody."""
    tenant = uuid.uuid4()
    async with _http() as http:
        me = (await http.get("/api/v1/me", headers=_headers(tenant, "boss", ORG_ADMIN))).json()
    assert me["viewsAllDepartments"] is True
    assert me["decidesAllDepartments"] is True


async def test_a_seat_holder_reports_neither_and_is_read_off_his_seats(
    app_session: AppSessionFactory,
) -> None:
    """Both flags false is the normal employee. The screen falls through to his
    seats, which is where his authority actually is, and a `dept_viewer` seat is
    what makes the two seat roles worth telling apart on the wire."""
    tenant = uuid.uuid4()
    sales = await _seat(app_session, tenant, "hos", SEAT_APPROVER)
    viewer_tenant = uuid.uuid4()
    viewer_dept = await _seat(app_session, viewer_tenant, "beobachter", SEAT_VIEWER)

    async with _http() as http:
        me = (await http.get("/api/v1/me", headers=_headers(tenant, "hos", MEMBER_ROLE))).json()
        assert me["viewsAllDepartments"] is False
        assert me["decidesAllDepartments"] is False
        assert [(s["departmentId"], s["seatRole"]) for s in me["seats"]] == [
            (str(sales), SEAT_APPROVER)
        ]

        watcher = (
            await http.get("/api/v1/me", headers=_headers(viewer_tenant, "beobachter", MEMBER_ROLE))
        ).json()
        assert [(s["departmentId"], s["seatRole"]) for s in watcher["seats"]] == [
            (str(viewer_dept), SEAT_VIEWER)
        ]


async def test_me_still_carries_what_the_principal_carried(
    app_session: AppSessionFactory,
) -> None:
    """This route returned the raw `Principal` until this slice. `scopes` was
    dropped in the rewrite -- a silent subtraction from a response an
    out-of-repo client may read -- and is back. The one rename that remains,
    `tenant_id` -> `tenantId`, is deliberate (every other body in this API is
    camelCase) and is pinned here so it is a decision rather than an accident.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        me = (await http.get("/api/v1/me", headers=_headers(tenant, "boss", ORG_ADMIN))).json()
    assert me["subject"] == "boss"
    assert me["role"] == ORG_ADMIN
    assert me["kind"] == "operator"
    assert me["tenantId"] == str(tenant)
    assert me["scopes"] == []
    assert "tenant_id" not in me
