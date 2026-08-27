"""`grant_seat`'s tri-state `agent_manage` composability, and what revoking a
seat takes with it.

Modelled on `tests/workspace/test_queue.py`: drive `oc8.workspace.members`
directly, because the bug every reviewer of the design found in a naive version
-- an ordinary, unrelated `seat_role` promotion or demotion silently RESETTING
or DROPPING an already-granted toggle the moment the caller omits the field --
is invisible through a route that returns 200 either way. A plain `bool = False`
default on `GrantSeatRequest.agent_manage` was tried and rejected in review for
exactly this reason; `test_omitting_agent_manage_on_seat_role_change_does_not_
reset_it` is the direct regression pin for that fix, and a weak version of it is
worse than none -- see the design's own words on this file's three hardest
tests.

Every test mints its own tenant: `ACME_TENANT_ID` has no per-test rollback and
`org_member` carries `UNIQUE (tenant_id, subject)`.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.authz.permissions import SEAT_APPROVER, SEAT_VIEWER
from oc8.authz.scope import subject_uuid_for
from oc8.main import create_app
from oc8.workspace.members import grant_seat, revoke_seat
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID, subject: str, role: str = "org_admin") -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=role)
    return {"Authorization": f"Bearer {token}"}


async def _member(db: Any, tenant: uuid.UUID, subject: str) -> m.OrgMember:
    row: m.OrgMember | None = (
        await db.execute(
            select(m.OrgMember).where(
                m.OrgMember.tenant_id == tenant,
                m.OrgMember.subject == subject,
                m.OrgMember.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    row = m.OrgMember(tenant_id=tenant, subject=subject, subject_uuid=subject_uuid_for(subject))
    db.add(row)
    await db.flush()
    return row


async def _department(db: Any, tenant: uuid.UUID, name: str) -> uuid.UUID:
    dept = m.Department(tenant_id=tenant, name=name, frame={})
    db.add(dept)
    await db.flush()
    return dept.id


async def _live_seat(
    db: Any, tenant: uuid.UUID, member_id: uuid.UUID, department_id: uuid.UUID
) -> m.OrgMemberDepartment | None:
    row: m.OrgMemberDepartment | None = (
        await db.execute(
            select(m.OrgMemberDepartment).where(
                m.OrgMemberDepartment.tenant_id == tenant,
                m.OrgMemberDepartment.member_id == member_id,
                m.OrgMemberDepartment.department_id == department_id,
                m.OrgMemberDepartment.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    return row


# ---------------------------------------------------------------------- test 9


async def test_promoting_seat_role_preserves_agent_manage(app_session: AppSessionFactory) -> None:
    """`dept_viewer`+`agent_manage=True`, promoted to `dept_approver` with the
    caller passing `agent_manage=None` explicitly -- the tri-state's whole
    point: `None` reads as "leave unchanged", not as "unset it"."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        anna = await _member(db, tenant, "anna")
        sales = await _department(db, tenant, "Vertrieb")
        first, created = await grant_seat(
            db,
            tenant_id=tenant,
            member_id=anna.id,
            department_id=sales,
            seat_role=SEAT_VIEWER,
            agent_manage=True,
            granted_by=None,
        )
        assert created is True
        assert first.agent_manage is True

        promoted, changed = await grant_seat(
            db,
            tenant_id=tenant,
            member_id=anna.id,
            department_id=sales,
            seat_role=SEAT_APPROVER,
            agent_manage=None,
            granted_by=None,
        )
        assert changed is True
        assert promoted.seat_role == SEAT_APPROVER
        assert promoted.agent_manage is True, (
            "the promotion passed agent_manage=None (leave-unchanged) and must not "
            "have reset the toggle a naive revoke-and-recreate would silently drop"
        )
        assert promoted.id != first.id, "a seat_role change is revoke-and-recreate, not an update"

    async with app_session(tenant) as db:
        live = await _live_seat(db, tenant, anna.id, sales)
        assert live is not None
        assert live.seat_role == SEAT_APPROVER
        assert live.agent_manage is True

        old = await db.get(m.OrgMemberDepartment, first.id)
        assert old is not None and old.revoked_at is not None


# --------------------------------------------------------------------- test 10


async def test_toggling_agent_manage_alone_persists_and_is_audited(
    app_session: AppSessionFactory,
) -> None:
    """Same `seat_role` throughout, flip `agent_manage` `None -> True` through
    the real route: the row changes, `changed=True`'s HTTP-visible effect is a
    written audit event, and that event's `resource` now names `agent_manage` --
    not only on a toggle-only change, per the design, so an admin reading the log
    can see current state regardless of what changed."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        anna = await _member(db, tenant, "anna")
        sales = await _department(db, tenant, "Vertrieb")
        seat, _created = await grant_seat(
            db,
            tenant_id=tenant,
            member_id=anna.id,
            department_id=sales,
            seat_role=SEAT_VIEWER,
            agent_manage=False,
            granted_by=None,
        )
        assert seat.agent_manage is False
        anna_id, sales_id = anna.id, sales

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
            r = await http.put(
                f"/api/v1/members/{anna_id}/departments/{sales_id}",
                headers=_headers(tenant, "die-admin"),
                json={"seatRole": SEAT_VIEWER, "agentManage": True},
            )
            assert r.status_code == 200, r.text

    async with app_session(tenant) as db:
        live = await _live_seat(db, tenant, anna_id, sales_id)
        assert live is not None
        assert live.agent_manage is True

        events = (
            (
                await db.execute(
                    select(m.AuditEvent).where(
                        m.AuditEvent.tenant_id == tenant,
                        m.AuditEvent.action == "member.seat_granted",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert events, "toggling agent_manage alone must still write an audit event"
        last = events[-1]
        assert last.resource.get("agent_manage") is True, (
            f"the audit event's resource does not name agent_manage: {last.resource}"
        )


# --------------------------------------------------------------------- test 11


async def test_omitting_agent_manage_on_seat_role_change_does_not_reset_it(
    app_session: AppSessionFactory,
) -> None:
    """The regression pin, at the LOWEST level the bug can hide: call `grant_seat`
    for a `seat_role` DEMOTION with `agent_manage` genuinely OMITTED from the
    call -- not passed as `None` by an explicit keyword, relying on the
    function's own default -- exactly the shape a caller who has never heard of
    the toggle would write. A `bool = False` default (the version review
    rejected) makes this silently strip the grant; the tri-state default must
    not."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        anna = await _member(db, tenant, "anna")
        sales = await _department(db, tenant, "Vertrieb")
        await grant_seat(
            db,
            tenant_id=tenant,
            member_id=anna.id,
            department_id=sales,
            seat_role=SEAT_APPROVER,
            agent_manage=True,
            granted_by=None,
        )

        # `agent_manage` is not in this call at all -- the omission itself is
        # the thing under test, not merely an explicit `None`.
        demoted, changed = await grant_seat(
            db,
            tenant_id=tenant,
            member_id=anna.id,
            department_id=sales,
            seat_role=SEAT_VIEWER,
            granted_by=None,
        )
        assert changed is True
        assert demoted.agent_manage is True, (
            "omitting agent_manage on a seat_role change reset it to False -- the "
            "exact naive-default bug review rejected"
        )

    async with app_session(tenant) as db:
        live = await _live_seat(db, tenant, anna.id, sales)
        assert live is not None
        assert live.seat_role == SEAT_VIEWER
        assert live.agent_manage is True


# --------------------------------------------------------------------- test 12


async def test_revoking_the_seat_revokes_agent_manage_with_it(
    app_session: AppSessionFactory,
) -> None:
    """No separate revocation path for the toggle to drift from: `revoked_at`
    empties `agent_manage_departments` on the very next resolve, same as it
    empties everything else the row granted. Proven at a real door -- a write
    route the seat used to admit her to -- not only by reading the row back."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        anna = await _member(db, tenant, "anna")
        sales = await _department(db, tenant, "Vertrieb")
        agent = m.Agent(
            tenant_id=tenant,
            department_id=sales,
            name="Nora",
            status="stopped",
            narrowing={},
            definition={},
            presentation={},
        )
        db.add(agent)
        await db.flush()
        await grant_seat(
            db,
            tenant_id=tenant,
            member_id=anna.id,
            department_id=sales,
            seat_role=SEAT_VIEWER,
            agent_manage=True,
            granted_by=None,
        )
        anna_id, sales_id, agent_id = anna.id, sales, agent.id

    async with app_session(tenant) as db:
        revoked = await revoke_seat(db, tenant_id=tenant, member_id=anna_id, department_id=sales_id)
        assert revoked is not None
        assert revoked.revoked_at is not None

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
            r = await http.post(
                f"/api/v1/agents/{agent_id}/lifecycle",
                json={"action": "start"},
                headers=_headers(tenant, "anna", "member"),
            )
            assert r.status_code in (403, 404), (
                f"a revoked seat's holder can still write an agent: {r.status_code} {r.text}"
            )
