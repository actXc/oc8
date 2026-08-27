"""The seat-administration doors, past the two cases §8 names.

§8 asserts that only an `org_admin` may grant a seat (test 25) and that revoking
one bites on the next request (test 26). Everything below is a way this surface
can be wrong while both of those still pass -- and each one is authority, not
cosmetics: a promotion that writes a second live row, a grant into a department
that does not exist, a defaulted POST body that quietly strips a CEO of his
company-wide view, a members list that crosses a tenant boundary.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.authz.permissions import SEAT_APPROVER, SEAT_VIEWER
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
    approval: uuid.UUID
    hos: uuid.UUID


async def _office(app_session: AppSessionFactory) -> _Office:
    office = _Office()
    office.tenant = uuid.uuid4()
    async with app_session(office.tenant) as db:
        sales = m.Department(tenant_id=office.tenant, name="Vertrieb")
        db.add(sales)
        await db.flush()
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
        office.sales, office.approval, office.hos = sales.id, approval.id, member.id
    return office


async def _seat_rows(
    app_session: AppSessionFactory, tenant: uuid.UUID, member_id: uuid.UUID
) -> list[Any]:
    async with app_session(tenant) as db:
        return list(
            (
                await db.execute(
                    select(m.OrgMemberDepartment).where(
                        m.OrgMemberDepartment.member_id == member_id
                    )
                )
            )
            .scalars()
            .all()
        )


async def test_promoting_a_seat_leaves_exactly_one_live_row_and_real_authority(
    app_session: AppSessionFactory,
) -> None:
    """A promotion is a revoke followed by a grant, in that order.

    `uq_org_member_department_live` is partial on `revoked_at IS NULL`, so the
    obvious implementation -- insert the new seat -- raises a unique violation,
    and the second-most obvious one -- update the row in place -- loses the answer
    to "and until when was he only a viewer". Both halves are asserted: the row
    count AND the authority, because a seat table that looks right while the
    decide door still refuses him is the same outage with better bookkeeping.
    """
    office = await _office(app_session)
    admin = _headers(office.tenant, "boss", "org_admin")
    his = _headers(office.tenant, "hos", "member")
    seat = f"/api/v1/members/{office.hos}/departments/{office.sales}"

    async with _http() as http:
        viewer = await http.put(seat, json={"seatRole": SEAT_VIEWER}, headers=admin)
        assert viewer.status_code < 300, viewer.text
        assert [s["seatRole"] for s in viewer.json()["seats"]] == [SEAT_VIEWER]

        # A viewer reads the queue and cannot empty it.
        assert (await http.get("/api/v1/approvals", headers=his)).status_code == 200
        refused = await http.post(
            f"/api/v1/approvals/{office.approval}/decision",
            json={"decision": "approve"},
            headers=his,
        )
        assert refused.status_code == 403, refused.text

        promoted = await http.put(seat, json={"seatRole": SEAT_APPROVER}, headers=admin)
        assert promoted.status_code < 300, promoted.text
        assert [s["seatRole"] for s in promoted.json()["seats"]] == [SEAT_APPROVER]

        # Same token, next request.
        decided = await http.post(
            f"/api/v1/approvals/{office.approval}/decision",
            json={"decision": "approve"},
            headers=his,
        )
        assert decided.status_code == 200, decided.text

    rows = await _seat_rows(app_session, office.tenant, office.hos)
    live = [r for r in rows if r.revoked_at is None]
    assert len(live) == 1 and live[0].seat_role == SEAT_APPROVER
    revoked = [r for r in rows if r.revoked_at is not None]
    assert len(revoked) == 1 and revoked[0].seat_role == SEAT_VIEWER, (
        "the viewer seat was overwritten instead of revoked; 'who could see this, "
        "and until when' is no longer answerable"
    )


async def test_re_granting_the_same_seat_does_not_churn_the_row(
    app_session: AppSessionFactory,
) -> None:
    """An idempotent PUT is a PUT. Re-issuing an identical seat must not write a
    revoke/grant pair for an act nobody performed -- an audit trail that records
    two authority changes every time somebody clicks the same button twice is one
    nobody can read a real change out of."""
    office = await _office(app_session)
    admin = _headers(office.tenant, "boss", "org_admin")
    seat = f"/api/v1/members/{office.hos}/departments/{office.sales}"

    async with _http() as http:
        first = await http.put(seat, json={"seatRole": SEAT_APPROVER}, headers=admin)
        assert first.status_code < 300, first.text
        again = await http.put(seat, json={"seatRole": SEAT_APPROVER}, headers=admin)
        assert again.status_code < 300, again.text

    rows = await _seat_rows(app_session, office.tenant, office.hos)
    assert len(rows) == 1 and rows[0].revoked_at is None


async def test_a_seat_cannot_be_written_into_a_department_that_is_not_there(
    app_session: AppSessionFactory,
) -> None:
    """There is no foreign key on `org_member_department.department_id` -- the
    seat deliberately outlives an archived department -- so nothing but this check
    stands between a typo and a seat that matches no approval, appears on no
    screen, and cannot be diagnosed from the row.

    The third case is the one that matters most: another TENANT's department id.
    """
    office = await _office(app_session)
    admin = _headers(office.tenant, "boss", "org_admin")

    other_tenant = uuid.uuid4()
    async with app_session(other_tenant) as db:
        foreign = m.Department(tenant_id=other_tenant, name="Fremd")
        db.add(foreign)
        await db.flush()
        foreign_id = foreign.id

    async with app_session(office.tenant) as db:
        archived = m.Department(tenant_id=office.tenant, name="Aufgelöst")
        db.add(archived)
        await db.flush()
        archived.deleted_at = dt.datetime.now(dt.UTC)
        archived_id = archived.id

    async with _http() as http:
        for department_id, why in (
            (uuid.uuid4(), "a department that never existed"),
            (archived_id, "an archived department"),
            (foreign_id, "another tenant's department"),
        ):
            got = await http.put(
                f"/api/v1/members/{office.hos}/departments/{department_id}",
                json={"seatRole": SEAT_APPROVER},
                headers=admin,
            )
            assert got.status_code == 404, f"{why}: {got.status_code} {got.text}"

        unknown_role = await http.put(
            f"/api/v1/members/{office.hos}/departments/{office.sales}",
            json={"seatRole": "dept_manager"},
            headers=admin,
        )
        assert unknown_role.status_code == 422, unknown_role.text
        assert "dept_manager" in unknown_role.text

    assert await _seat_rows(app_session, office.tenant, office.hos) == [], (
        "one of the four refusals wrote a seat anyway"
    )


async def test_revoking_a_seat_nobody_holds_says_so(app_session: AppSessionFactory) -> None:
    """404 rather than a cheerful 204. An administrator who mistypes the
    department and is told "done" walks away believing an authority was taken
    away that is still held."""
    office = await _office(app_session)
    admin = _headers(office.tenant, "boss", "org_admin")

    async with _http() as http:
        never = await http.delete(
            f"/api/v1/members/{office.hos}/departments/{office.sales}", headers=admin
        )
        assert never.status_code == 404, never.text

        granted = await http.put(
            f"/api/v1/members/{office.hos}/departments/{office.sales}",
            json={"seatRole": SEAT_APPROVER},
            headers=admin,
        )
        assert granted.status_code < 300
        assert (
            await http.delete(
                f"/api/v1/members/{office.hos}/departments/{office.sales}", headers=admin
            )
        ).status_code < 300
        twice = await http.delete(
            f"/api/v1/members/{office.hos}/departments/{office.sales}", headers=admin
        )
        assert twice.status_code == 404, twice.text

        stranger = await http.delete(
            f"/api/v1/members/{uuid.uuid4()}/departments/{office.sales}", headers=admin
        )
        assert stranger.status_code == 404, stranger.text


async def test_creating_a_member_adopts_the_row_the_gate_already_minted(
    app_session: AppSessionFactory,
) -> None:
    """The gate mints a person on their first request, so by the time an
    administrator enrols them the row usually exists. An INSERT here would be a
    unique violation -- a 500 for doing the obvious thing -- and a silent no-op
    would drop the display name they just typed."""
    office = await _office(app_session)
    admin = _headers(office.tenant, "boss", "org_admin")

    async with _http() as http:
        # `newjoiner` reaches the system first, which mints their row.
        seen = await http.get("/api/v1/me", headers=_headers(office.tenant, "newjoiner", "member"))
        assert seen.status_code == 200
        minted_id = seen.json()["memberId"]

        created = await http.post(
            "/api/v1/members",
            json={"subject": "newjoiner", "displayName": "Neue Kollegin"},
            headers=admin,
        )
        assert created.status_code == 200, (
            f"a row that already existed was reported as created: {created.text}"
        )
        assert created.json()["id"] == minted_id
        assert created.json()["displayName"] == "Neue Kollegin"

        fresh = await http.post(
            "/api/v1/members",
            json={"subject": "never-seen", "displayName": "Vorbereitet"},
            headers=admin,
        )
        assert fresh.status_code == 201, fresh.text


async def test_a_defaulted_post_body_does_not_strip_a_company_wide_view(
    app_session: AppSessionFactory,
) -> None:
    """`allDepartments` is only ever widened here.

    It defaults to `false`, and it is the ONLY unrestricted term the messenger
    door can read -- a Telegram message carries no token. If an upsert wrote the
    default through, an administrator correcting somebody's display name would
    silently stop the CEO's phone from being able to decide anything, and nothing
    anywhere would say so.
    """
    office = await _office(app_session)
    admin = _headers(office.tenant, "boss", "org_admin")

    async with _http() as http:
        ceo = await http.post(
            "/api/v1/members",
            json={"subject": "ceo", "displayName": "Chefin", "allDepartments": True},
            headers=admin,
        )
        assert ceo.status_code == 201, ceo.text
        assert ceo.json()["allDepartments"] is True

        renamed = await http.post(
            "/api/v1/members",
            json={"subject": "ceo", "displayName": "Chefin (neu)"},
            headers=admin,
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["allDepartments"] is True, (
            "a defaulted body took the company-wide view away"
        )
        assert renamed.json()["displayName"] == "Chefin (neu)"


async def test_the_members_list_stops_at_the_tenant_boundary(
    app_session: AppSessionFactory,
) -> None:
    """End-to-end, and honest about what is holding it up.

    RLS is what enforces this today: `list_members`' explicit `tenant_id`
    predicate is redundant under a bound session, and there is no way to exercise
    it from here -- an UNBOUND session fails closed and returns nothing either. So
    this is a boundary test of the door, not a test of that predicate, and it is
    here because the door is new: `GET /members` returns the list an administrator
    uses to decide who may release money, and "it is obviously scoped" is what was
    said about `GET /approvals` too.
    """
    office = await _office(app_session)
    other = uuid.uuid4()
    async with app_session(other) as db:
        db.add(
            m.OrgMember(
                tenant_id=other,
                subject="somebody-elses-employee",
                subject_uuid=_subject_uuid("somebody-elses-employee"),
                display_name="Fremd",
            )
        )
        await db.flush()

    async with _http() as http:
        listed = await http.get(
            "/api/v1/members", headers=_headers(office.tenant, "b", "org_admin")
        )
    assert listed.status_code == 200, listed.text
    subjects = {r["subject"] for r in listed.json()["items"]}
    assert "hos" in subjects
    assert "somebody-elses-employee" not in subjects
