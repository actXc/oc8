"""Who may put somebody in a department, and how fast taking it away works.

`member:manage` is `org_admin`-only, deliberately in no seat vocabulary and not
in `_DEPT_MANAGER` -- which otherwise holds nine `:manage` grants. A Head of
Sales who can enrol himself in Engineering is the department boundary in a
different coat, and it would be reachable through the very screen this slice
exists to give him.

The second test is the one that justifies keeping seats out of the JWT. Every
alternative -- a claim, a cached set, a five-minute TTL -- means that revoking
the authority to release money takes effect somewhere between now and the next
token refresh, and nobody can say which.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.authz.permissions import SEAT_APPROVER
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


def _subject_uuid(subject: str) -> uuid.UUID:
    try:
        return uuid.UUID(subject)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"oc8:subject:{subject}")


class _Office:
    tenant: uuid.UUID
    sales: uuid.UUID
    engineering: uuid.UUID
    approval: uuid.UUID
    hos: uuid.UUID


async def _office(app_session: AppSessionFactory) -> _Office:
    """Two departments, a pending approval in Vertrieb, and a member `hos` who
    already holds a dept_approver seat there."""
    office = _Office()
    office.tenant = uuid.uuid4()
    async with app_session(office.tenant) as db:
        sales = m.Department(tenant_id=office.tenant, name="Vertrieb")
        engineering = m.Department(tenant_id=office.tenant, name="Entwicklung")
        db.add_all([sales, engineering])
        await db.flush()
        office.sales, office.engineering = sales.id, engineering.id

        agent = m.Agent(tenant_id=office.tenant, department_id=sales.id, name="Nora")
        db.add(agent)
        await db.flush()
        approval = m.ApprovalRequest(
            tenant_id=office.tenant,
            agent_id=agent.id,
            department_id=sales.id,
            action_type="tool_send",
            status="pending",
            title="Angebot Gartenholz GmbH",
            detail="",
            amount_text="4.320,00 EUR",
            payload={"tool": "odoo.send_quotation", "arguments": {}},
        )
        db.add(approval)

        member = m.OrgMember(
            tenant_id=office.tenant,
            subject="hos",
            subject_uuid=_subject_uuid("hos"),
            display_name="Head of Sales",
        )
        db.add(member)
        await db.flush()
        db.add(
            m.OrgMemberDepartment(
                tenant_id=office.tenant,
                member_id=member.id,
                department_id=sales.id,
                seat_role=SEAT_APPROVER,
            )
        )
        await db.flush()
        office.approval, office.hos = approval.id, member.id
    return office


# ----------------------------------------------------------------------- 25


async def test_only_an_org_admin_may_grant_a_seat(app_session: AppSessionFactory) -> None:
    office = await _office(app_session)
    seat = f"/api/v1/members/{office.hos}/departments/{office.engineering}"

    async with _http() as http:
        # The Head of Sales, enrolling himself in Engineering. This is the hole
        # this whole slice exists to close, arriving through the front door.
        himself = await http.put(
            seat,
            json={"seatRole": SEAT_APPROVER},
            headers=_headers(office.tenant, "hos", "member"),
        )
        assert himself.status_code == 403, himself.text

        # An operator runs the office day to day and still may not.
        operator = await http.put(
            seat,
            json={"seatRole": SEAT_APPROVER},
            headers=_headers(office.tenant, "op-1", "operator"),
        )
        assert operator.status_code == 403, operator.text

        # `dept_manager` holds nine `:manage` grants and must not hold this one.
        manager = await http.put(
            seat,
            json={"seatRole": SEAT_APPROVER},
            headers=_headers(office.tenant, "dm-1", "dept_manager"),
        )
        assert manager.status_code == 403, manager.text

        # Creating a person is the same permission.
        created = await http.post(
            "/api/v1/members",
            json={"subject": "smuggled", "displayName": "Smuggled", "allDepartments": True},
            headers=_headers(office.tenant, "hos", "member"),
        )
        assert created.status_code == 403, created.text

        revoked = await http.delete(seat, headers=_headers(office.tenant, "hos", "member"))
        assert revoked.status_code == 403, revoked.text

        # And the control: the administrator can, so the 403s above are a
        # permission and not a broken route.
        admin = await http.put(
            seat,
            json={"seatRole": SEAT_APPROVER},
            headers=_headers(office.tenant, "boss", "org_admin"),
        )
        assert admin.status_code < 300, admin.text

    async with app_session(office.tenant) as db:
        seats = (await db.execute(_live_seats(office.hos))).scalars().all()
        assert {s.department_id for s in seats} == {office.sales, office.engineering}, (
            "one of the three refusals above wrote a seat anyway"
        )
        added = next(s for s in seats if s.department_id == office.engineering)
        assert added.seat_role == SEAT_APPROVER


def _live_seats(member_id: uuid.UUID) -> Any:
    from sqlalchemy import select

    return select(m.OrgMemberDepartment).where(
        m.OrgMemberDepartment.member_id == member_id,
        m.OrgMemberDepartment.revoked_at.is_(None),
    )


# ----------------------------------------------------------------------- 26


async def test_revoking_a_seat_takes_effect_on_the_next_request(
    app_session: AppSessionFactory,
) -> None:
    """No token refresh, no TTL wait, no logout.

    The same bearer string is sent before and after, and it is bound to a local
    variable here so the test cannot accidentally mint a second one -- a fresh
    token would make this test pass for the wrong reason and prove nothing about
    where seats live.
    """
    office = await _office(app_session)
    his_token = _headers(office.tenant, "hos", "member")
    admin = _headers(office.tenant, "boss", "org_admin")
    seat = f"/api/v1/members/{office.hos}/departments/{office.sales}"

    async with _http() as http:
        before = await http.get("/api/v1/approvals", headers=his_token)
        assert before.status_code == 200, before.text
        assert [r["id"] for r in before.json()] == [str(office.approval)]

        listed = await http.get("/api/v1/members", headers=admin)
        assert listed.status_code == 200, listed.text
        mine = next(r for r in listed.json()["items"] if r["id"] == str(office.hos))
        assert [s["departmentId"] for s in mine["seats"]] == [str(office.sales)]
        assert mine["seats"][0]["seatRole"] == SEAT_APPROVER
        assert mine["seats"][0]["departmentName"] == "Vertrieb"

        gone = await http.delete(seat, headers=admin)
        assert gone.status_code < 300, gone.text

        # Same token. Next request.
        after = await http.get("/api/v1/approvals", headers=his_token)
        assert after.status_code == 403, (
            f"a revoked approver was still admitted: {after.status_code} {after.text}"
        )
        refused = await http.post(
            f"/api/v1/approvals/{office.approval}/decision",
            json={"decision": "approve"},
            headers=his_token,
        )
        assert refused.status_code == 403, refused.text

        after_list = await http.get("/api/v1/members", headers=admin)
        mine_now = next(r for r in after_list.json()["items"] if r["id"] == str(office.hos))
        assert mine_now["seats"] == []

    async with app_session(office.tenant) as db:
        row = await db.get(m.ApprovalRequest, office.approval)
        assert row is not None and row.status == "pending"
        # Revoked, not deleted: "who could approve, and until when" is the first
        # question an audit asks, and a DELETE answers it with silence.
        from sqlalchemy import select

        history = (
            (
                await db.execute(
                    select(m.OrgMemberDepartment).where(
                        m.OrgMemberDepartment.member_id == office.hos,
                        m.OrgMemberDepartment.department_id == office.sales,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(history) == 1
        assert history[0].revoked_at is not None
