"""The funnel's own refusal, with both doors taken out of the picture.

§0.B says the term is enforced in three places and that one of them "cannot
compile without it". `test_decide_requires_an_actor` pins the signature; the two
door-level tests pin the 404 and the messenger's flat refusal. Neither reaches
this: both doors check the scope BEFORE they call `decide_approval`, so the check
inside it could be deleted and every one of those tests would still pass.

Which is exactly the check that matters for the door written next year.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.approvals import NotYourDepartment, decide_approval
from oc8.auth.principal import Principal
from oc8.authz.permissions import SEAT_APPROVER
from oc8.authz.scope import HumanActor, scope_for_principal, subject_uuid_for
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def _office(db: Any, tenant: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, m.ApprovalRequest]:
    """(sales, engineering, one pending approval in ENGINEERING)."""
    sales = m.Department(tenant_id=tenant, name="Vertrieb")
    engineering = m.Department(tenant_id=tenant, name="Entwicklung")
    db.add_all([sales, engineering])
    await db.flush()
    agent = m.Agent(tenant_id=tenant, department_id=engineering.id, name="Ada")
    db.add(agent)
    await db.flush()
    approval = m.ApprovalRequest(
        tenant_id=tenant,
        agent_id=agent.id,
        department_id=engineering.id,
        action_type="tool_send",
        status="pending",
        title="Produktionszugang öffnen",
        detail="",
    )
    db.add(approval)
    await db.flush()
    return sales.id, engineering.id, approval


async def _seated_actor(
    db: Any, tenant: uuid.UUID, subject: str, department_id: uuid.UUID
) -> HumanActor:
    member = m.OrgMember(tenant_id=tenant, subject=subject, subject_uuid=subject_uuid_for(subject))
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
    principal = Principal(subject=subject, tenant_id=tenant, role="member")
    resolved, scope = await scope_for_principal(db, principal)
    assert resolved is not None
    return HumanActor(principal=principal, member=resolved, scope=scope)


async def test_the_funnel_refuses_a_foreign_department_with_nothing_written(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales, _engineering, approval = await _office(db, tenant)
        actor = await _seated_actor(db, tenant, "hos", sales)
        approval_id = approval.id

        with pytest.raises(NotYourDepartment):
            await decide_approval(db, approval, decision="approve", tenant_id=tenant, actor=actor)

    async with app_session(tenant) as db:
        row = await db.get(m.ApprovalRequest, approval_id)
        assert row is not None
        assert row.status == "pending"
        assert row.decided_at is None and row.decided_by is None
        # No audit line either. A refused decision that still appends "approval.
        # approve" to the hash chain is a trail that says somebody approved this.
        rows = (await db.execute(m.AuditEvent.__table__.select())).all()
        assert rows == []


async def test_the_refusal_does_not_depend_on_the_row_being_pending(
    app_session: AppSessionFactory,
) -> None:
    """The scope is checked before the STATUS is, in the funnel too.

    `AlreadyDecided` carries the status and the time it was decided. If the
    funnel looked at the row's state first, a caller who reached it with a
    foreign approval would learn both -- which is the oracle the route's 404
    closes at the other layer.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales, _engineering, approval = await _office(db, tenant)
        approval.status = "approved"
        await db.flush()
        actor = await _seated_actor(db, tenant, "hos", sales)

        with pytest.raises(NotYourDepartment):
            await decide_approval(db, approval, decision="approve", tenant_id=tenant, actor=actor)


async def test_the_same_actor_decides_his_own_department(
    app_session: AppSessionFactory,
) -> None:
    """The control. Without it, "raises NotYourDepartment" is satisfied by a
    funnel that refuses everybody."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        _sales, engineering, approval = await _office(db, tenant)
        actor = await _seated_actor(db, tenant, "cto", engineering)

        result = await decide_approval(
            db, approval, decision="approve", tenant_id=tenant, actor=actor
        )
        assert result.approval.status == "approved"
        assert result.approval.decided_by == actor.member.id
