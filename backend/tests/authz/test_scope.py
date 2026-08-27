"""`DepartmentScope`'s third private frozenset (`_agent_manage`), and the two
accessors built on it -- `may_manage_agents` and `agent_manage_departments`.

Modelled on `tests/authz/test_scope_resolution.py`'s style, but this file is
about ONE property that no earlier test can see: that WRITE authority over an
Agent is a second, independent axis riding the same seat row as READ/DECIDE,
and that it can never be wider than what the seat already lets somebody see.

`_resolved_scope` is still module-private and the constructor still refuses
everybody (`authz/scope.py`'s own doctrine), so every scope here is produced the
one legal way: `scope_for_principal`, from rows written through the ORM.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.auth.principal import Principal
from oc8.authz.permissions import SEAT_APPROVER, SEAT_VIEWER
from oc8.authz.scope import scope_for_principal, subject_uuid_for
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _principal(tenant: uuid.UUID, subject: str, *, role: str = "member") -> Principal:
    return Principal(subject=subject, tenant_id=tenant, role=role, kind="operator")


async def _department(db: Any, tenant: uuid.UUID, name: str) -> uuid.UUID:
    row = m.Department(tenant_id=tenant, name=name)
    db.add(row)
    await db.flush()
    return row.id


async def _seat(
    db: Any,
    tenant: uuid.UUID,
    member: m.OrgMember,
    department_id: uuid.UUID,
    seat_role: str,
    *,
    agent_manage: bool = False,
) -> None:
    db.add(
        m.OrgMemberDepartment(
            tenant_id=tenant,
            member_id=member.id,
            department_id=department_id,
            seat_role=seat_role,
            agent_manage=agent_manage,
        )
    )
    await db.flush()


async def test_agent_manage_departments_is_independent_of_seat_role(
    app_session: AppSessionFactory,
) -> None:
    """The toggle and `seat_role` are two axes, not one. A `dept_viewer` who was
    handed the toggle may write agents in her department and still may not decide
    an approval there; a `dept_approver` who was NOT handed it may decide and may
    not write. Collapsing the two into "the stronger seat role also writes" is
    the naive version this test exists to refuse."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = await _department(db, tenant, "Vertrieb")
        engineering = await _department(db, tenant, "Entwicklung")
        member = m.OrgMember(
            tenant_id=tenant, subject="anna", subject_uuid=subject_uuid_for("anna")
        )
        db.add(member)
        await db.flush()
        # Viewer seat, WITH the toggle.
        await _seat(db, tenant, member, sales, SEAT_VIEWER, agent_manage=True)
        # Approver seat, WITHOUT the toggle.
        await _seat(db, tenant, member, engineering, SEAT_APPROVER, agent_manage=False)
        _resolved, scope = await scope_for_principal(db, _principal(tenant, "anna"))

    assert scope.may_manage_agents(sales) is True
    assert scope.may_decide(sales) is False, (
        "a dept_viewer holding the toggle must still not decide approvals -- the "
        "toggle is WRITE authority over Agent, nothing about approvals"
    )
    assert scope.may_manage_agents(engineering) is False
    assert scope.may_decide(engineering) is True, (
        "a dept_approver without the toggle must still decide approvals -- the "
        "toggle is an ADDITION, never a precondition for what a seat role already grants"
    )


async def test_agent_manage_is_a_structural_subset_of_viewable(
    app_session: AppSessionFactory,
) -> None:
    """`agent_manage_departments ⊆ viewable`, for every resolved scope -- not a
    coincidence of today's two seat roles (both of which happen to carry
    `approval:view`/now `agent:view`), but a structural property of how
    `_scope_from_seats` gates `_departments.add` on `carried or may_manage`."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = await _department(db, tenant, "Vertrieb")
        engineering = await _department(db, tenant, "Entwicklung")
        empty_dept = await _department(db, tenant, "Nichts")
        member = m.OrgMember(
            tenant_id=tenant, subject="anna", subject_uuid=subject_uuid_for("anna")
        )
        db.add(member)
        await db.flush()
        await _seat(db, tenant, member, sales, SEAT_VIEWER, agent_manage=True)
        await _seat(db, tenant, member, engineering, SEAT_APPROVER, agent_manage=False)
        # No seat at all in `empty_dept` -- present only to prove the subset
        # relation isn't trivially true because every department in the tenant
        # is viewable.
        _resolved, scope = await scope_for_principal(db, _principal(tenant, "anna"))

    assert scope.agent_manage_departments <= scope.viewable
    assert scope.agent_manage_departments == frozenset({sales})
    assert scope.viewable == frozenset({sales, engineering})
    assert empty_dept not in scope.viewable
    assert empty_dept not in scope.agent_manage_departments


async def test_unrestricted_does_not_imply_agent_manage(app_session: AppSessionFactory) -> None:
    """`approval:view_any` (the `auditor` built-in) makes a scope SEE every
    department. It must not also make it able to WRITE an agent in any of them --
    `_unrestricted` answers WHERE somebody may look, never WHAT they may do there,
    and `may_manage_agents` is a WHAT question the toggle alone answers."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        _member, scope = await scope_for_principal(
            db, _principal(tenant, "aud", role="auditor"), upsert=True
        )

    assert scope.is_unrestricted is True
    assert scope.decides_everywhere is False
    assert scope.may_manage_agents(uuid.uuid4()) is False
    assert scope.may_manage_agents(None) is False
    assert scope.agent_manage_departments == frozenset()
