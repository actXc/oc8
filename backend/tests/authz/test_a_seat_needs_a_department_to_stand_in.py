"""A seat in a department that has been archived, or that never existed, granted
authority anyway.

`_scope_from_seats` read `org_member_department` alone and never joined
`Department`, so `deleted_at` was not consulted: archiving a department narrowed
nobody. `PUT /members/{id}/departments/{id}` has always refused to SEAT anybody
in an archived department -- the read side simply did not hold the same position.

There is no foreign key on `org_member_department.department_id` (a seat outlives
an archived department on purpose, so the members screen can still show and
revoke it), which is also why a seat naming a department that does not exist at
all was representable: invisible on every screen, matching no approval, and a
place to stand that nothing could show.

The cost is named rather than discovered: pending approvals in an archived
department become answerable only by the unrestricted, who can still see them.
That is the fail-closed direction.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.auth.principal import Principal
from oc8.authz.permissions import SEAT_APPROVER, SEAT_PERMISSIONS
from oc8.authz.scope import check_two_level_vocabulary, scope_for_principal, subject_uuid_for
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _principal(tenant: uuid.UUID, subject: str, role: str = "member") -> Principal:
    return Principal(subject=subject, tenant_id=tenant, role=role, kind="operator")


async def _seated(db: Any, tenant: uuid.UUID, department_id: uuid.UUID) -> None:
    member = m.OrgMember(
        tenant_id=tenant,
        subject="hos",
        subject_uuid=subject_uuid_for("hos"),
        display_name="Head of Sales",
    )
    db.add(member)
    await db.flush()
    db.add(
        m.OrgMemberDepartment(
            tenant_id=tenant,
            member_id=member.id,
            department_id=department_id,
            seat_role=SEAT_APPROVER,
        )
    )
    await db.flush()


async def test_archiving_a_department_takes_its_seats_authority_with_it(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb")
        db.add(dept)
        await db.flush()
        await _seated(db, tenant, dept.id)

        # The control: while the department is live, the seat is authority.
        _who, before = await scope_for_principal(db, _principal(tenant, "hos"))
        assert before.may_decide(dept.id) is True

        dept.deleted_at = dt.datetime.now(tz=dt.UTC)
        await db.flush()

        _who, after = await scope_for_principal(db, _principal(tenant, "hos"))
        assert after.is_empty is True
        assert after.may_view(dept.id) is False
        assert after.may_decide(dept.id) is False


async def test_a_seat_in_a_department_that_does_not_exist_grants_nothing(
    app_session: AppSessionFactory,
) -> None:
    """There is no FK to stop one being written, so the read side has to."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _seated(db, tenant, uuid.uuid4())
        _who, scope = await scope_for_principal(db, _principal(tenant, "hos"))
    assert scope.is_empty is True


async def test_an_unrestricted_caller_still_covers_an_archived_department(
    app_session: AppSessionFactory,
) -> None:
    """The named cost, asserted so it stays true: the approvals filed in an
    archived department do not become unanswerable by everybody. `unrestricted`
    is a boolean and not a set of live departments, which is exactly why."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(
            tenant_id=tenant, name="Aufgelöst", deleted_at=dt.datetime.now(tz=dt.UTC)
        )
        db.add(dept)
        await db.flush()
        _who, scope = await scope_for_principal(
            db, _principal(tenant, "boss", role="org_admin"), upsert=True
        )
    assert scope.may_view(dept.id) is True
    assert scope.may_decide(dept.id) is True


def test_the_scope_refuses_a_seat_vocabulary_it_cannot_represent() -> None:
    """`DepartmentScope` carries TWO department sets, so it can only answer for a
    vocabulary with two levels. Nothing said so, and the gap was live though
    inert: `holds_anywhere` answers `clarification:answer` out of `_decide`, which
    `_scope_from_seats` builds from `approval:decide` ALONE. A third seat role
    carrying `clarification:answer` without `approval:decide` would have been
    refused at `require_departmental(CLARIFICATION_ANSWER)` while
    `SEAT_PERMISSIONS` -- and `GET /governance`, which renders it -- said the seat
    granted it.

    Checked at import against the real vocabulary; this exercises the check
    itself, because the real one passing proves only today.
    """
    # The real one, which is what the import-time call asserts.
    check_two_level_vocabulary(SEAT_PERMISSIONS)

    with pytest.raises(RuntimeError, match="neither the view level"):
        check_two_level_vocabulary(
            {
                **SEAT_PERMISSIONS,
                # Exactly the shape that used to slip through: an answerer who is
                # not an approver.
                "dept_answerer": frozenset(
                    {"approval:view", "clarification:view", "clarification:answer"}
                ),
            }
        )
    with pytest.raises(RuntimeError):
        # And the other direction: a seat that carries a permission no scope set
        # is built from at all.
        check_two_level_vocabulary({**SEAT_PERMISSIONS, "dept_boss": frozenset({"agent:manage"})})


async def test_the_two_screens_disagree_on_purpose_about_an_archived_seat(
    app_session: AppSessionFactory,
) -> None:
    """`/me` and `/members` ask different questions and must answer differently.

    `/me` answers WHERE THIS PERSON STANDS, and after the join above a seat in an
    archived department grants nothing. Listing it there would put the workspace
    in the wrong empty state -- `seats` non-empty reads as "nothing is waiting for
    you" rather than "nobody has assigned you to a department", which §7 calls the
    distinction that matters more than anything else on the page.

    `/members` answers WHAT IS ON RECORD, and must keep showing it: it is the
    screen that revokes the seat, and a row nobody can see is a row nobody takes
    away.
    """
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from oc8.auth import get_identity_provider
    from oc8.main import create_app

    @asynccontextmanager
    async def _http() -> AsyncIterator[AsyncClient]:
        app = create_app()
        async with LifespanManager(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                yield c

    def _headers(tenant: uuid.UUID, subject: str, role: str) -> dict[str, str]:
        token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=role)
        return {"Authorization": f"Bearer {token}"}

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(
            tenant_id=tenant, name="Aufgelöst", deleted_at=dt.datetime.now(tz=dt.UTC)
        )
        db.add(dept)
        await db.flush()
        await _seated(db, tenant, dept.id)

    async with _http() as http:
        me = (await http.get("/api/v1/me", headers=_headers(tenant, "hos", "member"))).json()
        assert me["seats"] == [], (
            "a seat that grants nothing was reported as standing somewhere, so "
            "the workspace shows the wrong one of its two empty states"
        )
        gov = (
            await http.get("/api/v1/governance", headers=_headers(tenant, "hos", "member"))
        ).json()
        assert gov["seats"] == []

        listed = (
            await http.get("/api/v1/members", headers=_headers(tenant, "admin", "org_admin"))
        ).json()
        rows = [r for r in listed["items"] if r["subject"] == "hos"]
        assert len(rows) == 1
        assert [s["departmentId"] for s in rows[0]["seats"]] == [str(dept.id)], (
            "the administrator can no longer see the seat he has to revoke"
        )
