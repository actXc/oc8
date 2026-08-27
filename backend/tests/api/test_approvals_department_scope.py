"""Whose approvals a person is shown, and whose they may answer.

Today `GET /approvals` (`api/v1/feed.py:66`) filters on status alone and hands
back every pending approval in the tenant, and `POST /approvals/{id}/decision`
(`api/v1/approvals.py:36`) loads the row with `db.get`, checks a permission, and
never asks whose approval it is. A company that gives its Head of Sales an
account today gives him the whole company.

Two of these are guards rather than features:

* **`test_a_sales_approver_cannot_decide_an_engineering_approval`** asserts 404
  AND that the row is still pending afterwards. A 403 with the decision already
  written would satisfy a status-code-only test.
* **`test_an_out_of_scope_decided_approval_404s_rather_than_409s`** -- the scope
  is resolved BEFORE the row's state is examined, so `AlreadyDecided`'s 409
  (which carries the status and the decision time) cannot be used as an
  existence oracle by somebody holding approval ids off the tenant-wide realtime
  socket.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.approvals.repo import visible_approvals
from oc8.auth import get_identity_provider
from oc8.auth.principal import Principal
from oc8.authz.permissions import SEAT_APPROVER, SEAT_VIEWER
from oc8.authz.scope import HumanActor, scope_for_principal
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


async def _department(db: Any, tenant: uuid.UUID, name: str) -> uuid.UUID:
    row = m.Department(tenant_id=tenant, name=name)
    db.add(row)
    await db.flush()
    return row.id


async def _agent(db: Any, tenant: uuid.UUID, department_id: uuid.UUID, name: str) -> uuid.UUID:
    row = m.Agent(tenant_id=tenant, department_id=department_id, name=name)
    db.add(row)
    await db.flush()
    return row.id


async def _approval(
    db: Any,
    tenant: uuid.UUID,
    *,
    agent_id: uuid.UUID,
    department_id: uuid.UUID | None,
    title: str,
    status: str = "pending",
) -> uuid.UUID:
    """A held tool call, written straight to the table.

    `action_type="tool_send"` with no task is the shape that decides cleanly:
    `resolve_tool_approval` finds no run, returns None, and
    `_abandon_unresumable` returns immediately because `task_id is None`. So this
    file tests the SCOPE and never the resume path, which has its own tests.
    """
    row = m.ApprovalRequest(
        tenant_id=tenant,
        agent_id=agent_id,
        department_id=department_id,
        action_type="tool_send",
        status=status,
        title=title,
        detail="",
        amount_text="4.320,00 EUR",
        payload={"tool": "odoo.send_quotation", "arguments": {"partner": "Gartenholz GmbH"}},
    )
    db.add(row)
    await db.flush()
    return row.id


async def _member(
    db: Any, tenant: uuid.UUID, *, subject: str, all_departments: bool = False
) -> m.OrgMember:
    """Get-or-create, because the GATE upserts a member row on the first
    request: a test that made a second one would trip `uq_org_member_subject`
    and look like a defect in the endpoint."""
    existing: m.OrgMember | None = (
        await db.execute(
            select(m.OrgMember).where(
                m.OrgMember.subject == subject, m.OrgMember.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = m.OrgMember(
        tenant_id=tenant,
        subject=subject,
        subject_uuid=_subject_uuid(subject),
        display_name=subject,
        all_departments=all_departments,
    )
    db.add(row)
    await db.flush()
    return row


async def _seat(
    db: Any, tenant: uuid.UUID, member: m.OrgMember, department_id: uuid.UUID, seat_role: str
) -> None:
    db.add(
        m.OrgMemberDepartment(
            tenant_id=tenant,
            member_id=member.id,
            department_id=department_id,
            seat_role=seat_role,
        )
    )
    await db.flush()


class _Office:
    """Two departments, an agent in each, one pending approval in each, plus a
    company-wide budget incident with no department at all."""

    tenant: uuid.UUID
    sales: uuid.UUID
    engineering: uuid.UUID
    sales_approval: uuid.UUID
    engineering_approval: uuid.UUID
    tenant_wide_approval: uuid.UUID


async def _office(app_session: AppSessionFactory) -> _Office:
    office = _Office()
    office.tenant = uuid.uuid4()
    async with app_session(office.tenant) as db:
        office.sales = await _department(db, office.tenant, "Vertrieb")
        office.engineering = await _department(db, office.tenant, "Entwicklung")
        nora = await _agent(db, office.tenant, office.sales, "Nora")
        ada = await _agent(db, office.tenant, office.engineering, "Ada")
        office.sales_approval = await _approval(
            db,
            office.tenant,
            agent_id=nora,
            department_id=office.sales,
            title="Angebot Gartenholz GmbH",
        )
        office.engineering_approval = await _approval(
            db,
            office.tenant,
            agent_id=ada,
            department_id=office.engineering,
            title="Produktionszugang öffnen",
        )
        office.tenant_wide_approval = await _approval(
            db,
            office.tenant,
            agent_id=ada,
            department_id=None,
            title="Token-Budget überschritten — tenant",
        )
    return office


async def _give_a_seat(
    app_session: AppSessionFactory,
    office: _Office,
    subject: str,
    department_id: uuid.UUID,
    seat_role: str = SEAT_APPROVER,
) -> uuid.UUID:
    async with app_session(office.tenant) as db:
        member = await _member(db, office.tenant, subject=subject)
        await _seat(db, office.tenant, member, department_id, seat_role)
        return member.id


async def _reload(app_session: AppSessionFactory, office: _Office, approval_id: uuid.UUID) -> Any:
    async with app_session(office.tenant) as db:
        return await db.get(m.ApprovalRequest, approval_id)


# ------------------------------------------------------------------------ 8


async def test_a_sales_approver_does_not_see_engineering_approvals(
    app_session: AppSessionFactory,
) -> None:
    """Engineering's approvals are not filtered out in the browser. They are
    never fetched."""
    office = await _office(app_session)
    await _give_a_seat(app_session, office, "hos", office.sales)

    async with _http() as http:
        got = await http.get("/api/v1/approvals", headers=_headers(office.tenant, "hos", "member"))
        assert got.status_code == 200, got.text
        assert [r["id"] for r in got.json()] == [str(office.sales_approval)]

        # `departmentId` INTERSECTS. It is a filter over what you may already
        # see, never a way to ask for somebody else's department.
        widened = await http.get(
            f"/api/v1/approvals?departmentId={office.engineering}",
            headers=_headers(office.tenant, "hos", "member"),
        )
        assert widened.status_code == 200, widened.text
        assert widened.json() == []

        narrowed = await http.get(
            f"/api/v1/approvals?departmentId={office.sales}",
            headers=_headers(office.tenant, "hos", "member"),
        )
        assert [r["id"] for r in narrowed.json()] == [str(office.sales_approval)]


# ------------------------------------------------------------------------ 9


async def test_a_sales_approver_cannot_decide_an_engineering_approval(
    app_session: AppSessionFactory,
) -> None:
    """404, and the row is still pending afterwards.

    The second half is the assertion that matters: a route that decided the
    approval and THEN refused would pass a status-code-only test while having
    already released the money.
    """
    office = await _office(app_session)
    await _give_a_seat(app_session, office, "hos", office.sales)
    headers = _headers(office.tenant, "hos", "member")

    async with _http() as http:
        refused = await http.post(
            f"/api/v1/approvals/{office.engineering_approval}/decision",
            json={"decision": "approve"},
            headers=headers,
        )
        assert refused.status_code == 404, refused.text

        # And the control: the same caller, the same shape of request, his own
        # department. Without this a 404 could just as well mean the endpoint is
        # broken for everybody.
        allowed = await http.post(
            f"/api/v1/approvals/{office.sales_approval}/decision",
            json={"decision": "approve"},
            headers=headers,
        )
        assert allowed.status_code == 200, allowed.text

    engineering = await _reload(app_session, office, office.engineering_approval)
    assert engineering is not None
    assert engineering.status == "pending", "the refused decision was written anyway"
    assert engineering.decided_at is None
    assert engineering.decided_by is None

    sales = await _reload(app_session, office, office.sales_approval)
    assert sales is not None and sales.status == "approved"


# ----------------------------------------------------------------------- 10


async def test_an_out_of_scope_decided_approval_404s_rather_than_409s(
    app_session: AppSessionFactory,
) -> None:
    """The oracle guard.

    409 carries the status and the time it was decided. If the row's state is
    examined before the scope is, anyone holding ids off the tenant-wide realtime
    socket -- which still announces `approval.created` for every department -- can
    ask this endpoint which of them exist and when they were answered.
    """
    office = await _office(app_session)
    await _give_a_seat(app_session, office, "hos", office.sales)
    headers = _headers(office.tenant, "hos", "member")

    async with app_session(office.tenant) as db:
        foreign = await db.get(m.ApprovalRequest, office.engineering_approval)
        assert foreign is not None
        foreign.status = "approved"
        mine = await db.get(m.ApprovalRequest, office.sales_approval)
        assert mine is not None
        mine.status = "approved"

    async with _http() as http:
        theirs = await http.post(
            f"/api/v1/approvals/{office.engineering_approval}/decision",
            json={"decision": "reject"},
            headers=headers,
        )
        assert theirs.status_code == 404, theirs.text
        body = theirs.text.lower()
        assert "approved" not in body and "already" not in body, (
            f"the refusal leaked the row's state: {theirs.text}"
        )

        # A never-existing id must be indistinguishable from a foreign one.
        nonexistent = await http.post(
            f"/api/v1/approvals/{uuid.uuid4()}/decision",
            json={"decision": "reject"},
            headers=headers,
        )
        assert nonexistent.status_code == 404
        assert nonexistent.json() == theirs.json()

        # And 409 still exists where it is honest: his OWN, already decided.
        conflict = await http.post(
            f"/api/v1/approvals/{office.sales_approval}/decision",
            json={"decision": "reject"},
            headers=headers,
        )
        assert conflict.status_code == 409, conflict.text


# ----------------------------------------------------------------------- 11


async def test_a_seatless_caller_sees_nothing_and_decides_nothing(
    app_session: AppSessionFactory,
) -> None:
    """A `member` token holds the empty set (`permissions_for()` -> frozenset()),
    so with no seat there is nothing to admit him on: 403 at the door on both
    routes, per §2's gate.

    The `[]` half of this lives one layer down, in the repository, and is
    asserted here directly: an empty scope must produce an EMPTY list, not an
    unfiltered one. `WHERE department_id IN ()` is not valid SQL, so the tempting
    shape -- "skip the filter when there are no departments" -- fails open and
    hands the whole tenant back.
    """
    office = await _office(app_session)

    async with app_session(office.tenant) as db:
        principal = Principal(subject="nobody", tenant_id=office.tenant, role="member")
        member, scope = await scope_for_principal(db, principal, upsert=True)
        assert member is not None and scope.is_empty is True
        actor = HumanActor(principal=principal, member=member, scope=scope)
        rows = await visible_approvals(db, actor=actor, status="pending")
        assert rows == [], "an empty scope must filter to nothing, not to everything"

    headers = _headers(office.tenant, "nobody", "member")
    async with _http() as http:
        listed = await http.get("/api/v1/approvals", headers=headers)
        assert listed.status_code == 403, listed.text
        decided = await http.post(
            f"/api/v1/approvals/{office.sales_approval}/decision",
            json={"decision": "approve"},
            headers=headers,
        )
        assert decided.status_code == 403, decided.text

    still = await _reload(app_session, office, office.sales_approval)
    assert still is not None and still.status == "pending"


# ----------------------------------------------------------------------- 12


async def test_an_org_admin_sees_every_department_and_the_tenant_wide_incident(
    app_session: AppSessionFactory,
) -> None:
    """Including `department_id IS NULL`, which is what a tenant-scope budget
    incident looks like. Nobody with a seat may see it; if the admin cannot
    either, the company's own budget breach is invisible to everyone."""
    office = await _office(app_session)

    async with _http() as http:
        got = await http.get(
            "/api/v1/approvals", headers=_headers(office.tenant, "boss", "org_admin")
        )
    assert got.status_code == 200, got.text
    assert {r["id"] for r in got.json()} == {
        str(office.sales_approval),
        str(office.engineering_approval),
        str(office.tenant_wide_approval),
    }


# ----------------------------------------------------------------------- 13


async def test_an_auditor_sees_everything_and_may_decide_nothing(
    app_session: AppSessionFactory,
) -> None:
    """`approval:view_any` and deliberately not `approval:decide_any`: an auditor
    who could only see the departments he holds a seat in could not audit the
    company, and an auditor who could decide would be auditing his own
    decisions."""
    office = await _office(app_session)
    headers = _headers(office.tenant, "auditor-1", "auditor")

    async with _http() as http:
        listed = await http.get("/api/v1/approvals", headers=headers)
        assert listed.status_code == 200, listed.text
        assert {r["id"] for r in listed.json()} == {
            str(office.sales_approval),
            str(office.engineering_approval),
            str(office.tenant_wide_approval),
        }

        decided = await http.post(
            f"/api/v1/approvals/{office.sales_approval}/decision",
            json={"decision": "approve"},
            headers=headers,
        )
        assert decided.status_code == 403, decided.text

    still = await _reload(app_session, office, office.sales_approval)
    assert still is not None and still.status == "pending"


# ----------------------------------------------------------------------- 14


async def test_an_operator_without_a_seat_is_narrowed_but_not_locked_out(
    app_session: AppSessionFactory,
) -> None:
    """The accepted behaviour change, pinned so nobody has to rediscover it.

    `operator` keeps `approval:decide`, so he is ADMITTED at the door -- 200 and
    404, never 403 -- but he holds no `decide_any`, so he is narrowed to his
    seats. Today that means an operator with no seat sees an empty queue, which
    is why the "you hold no seat" empty state exists: it has to read as
    configuration, not as breakage.
    """
    office = await _office(app_session)
    headers = _headers(office.tenant, "op-1", "operator")

    async with _http() as http:
        listed = await http.get("/api/v1/approvals", headers=headers)
        assert listed.status_code == 200, "an operator must not be refused at the door"
        assert listed.json() == []

        decided = await http.post(
            f"/api/v1/approvals/{office.sales_approval}/decision",
            json={"decision": "approve"},
            headers=headers,
        )
        assert decided.status_code == 404, decided.text

    # Give him the seat, change nothing else -- same token, no refresh.
    await _give_a_seat(app_session, office, "op-1", office.sales)

    async with _http() as http:
        listed = await http.get("/api/v1/approvals", headers=headers)
        assert [r["id"] for r in listed.json()] == [str(office.sales_approval)]
        decided = await http.post(
            f"/api/v1/approvals/{office.sales_approval}/decision",
            json={"decision": "approve"},
            headers=headers,
        )
        assert decided.status_code == 200, decided.text

    row = await _reload(app_session, office, office.sales_approval)
    assert row is not None and row.status == "approved"


async def test_a_viewer_seat_reads_the_queue_and_cannot_answer_it(
    app_session: AppSessionFactory,
) -> None:
    """Not in §8's list. `dept_viewer` is the seat role with no acting rights,
    and it exists only if the difference is enforced somewhere -- the list admits
    him, the decision does not."""
    office = await _office(app_session)
    await _give_a_seat(app_session, office, "watcher", office.sales, SEAT_VIEWER)
    headers = _headers(office.tenant, "watcher", "member")

    async with _http() as http:
        listed = await http.get("/api/v1/approvals", headers=headers)
        assert listed.status_code == 200, listed.text
        assert [r["id"] for r in listed.json()] == [str(office.sales_approval)]

        decided = await http.post(
            f"/api/v1/approvals/{office.sales_approval}/decision",
            json={"decision": "approve"},
            headers=headers,
        )
        assert decided.status_code == 403, decided.text

    row = await _reload(app_session, office, office.sales_approval)
    assert row is not None and row.status == "pending"
