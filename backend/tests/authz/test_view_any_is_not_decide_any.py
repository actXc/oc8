"""Seeing every department is not signing off in every department.

Not in §8's list, and it is the hole the gate fell into on the first pass.
`authz/permissions.py` gives `auditor` `approval:view_any` and deliberately not
`approval:decide_any` -- "an auditor who could decide would be auditing his own
decisions" -- but the scope collapsed both into ONE `unrestricted` flag. With one
flag:

* `scope.holds_anywhere(approval:decide)` answered True for an auditor, so
  `require_departmental(APPROVAL_DECIDE)` ADMITTED him at
  `POST /approvals/{id}/decision`; and
* `scope.may_decide(anything)` answered True too, so `decide_approval`'s
  defence-in-depth check waved him through as well.

Both layers, wrong the same way, from the same line. §8.13 catches it through
HTTP; this catches it in the term, where the fix is, and where a future
`require_departmental` on some other route would meet it again.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.auth.principal import Principal
from oc8.authz.permissions import APPROVAL_DECIDE, CLARIFICATION_ANSWER, CLARIFICATION_VIEW
from oc8.authz.scope import APPROVAL_VIEW, scope_for_principal, subject_uuid_for
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _principal(tenant: uuid.UUID, subject: str, role: str) -> Principal:
    return Principal(subject=subject, tenant_id=tenant, role=role, kind="operator")


async def _department(db: Any, tenant: uuid.UUID, name: str) -> uuid.UUID:
    row = m.Department(tenant_id=tenant, name=name)
    db.add(row)
    await db.flush()
    return row.id


async def test_an_auditor_sees_everywhere_and_signs_off_nowhere(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = await _department(db, tenant, "Vertrieb")
        _member, scope = await scope_for_principal(
            db, _principal(tenant, "auditor-1", "auditor"), upsert=True
        )

    assert scope.is_unrestricted is True
    assert scope.may_view(sales) is True
    assert scope.may_view(None) is True, "a tenant-wide budget incident is his to look at"

    assert scope.may_decide(sales) is False
    assert scope.may_decide(None) is False
    assert scope.holds_anywhere(APPROVAL_VIEW) is True
    assert scope.holds_anywhere(CLARIFICATION_VIEW) is True
    assert scope.holds_anywhere(APPROVAL_DECIDE) is False, (
        "the decide door admits an auditor, who holds approval:view_any and no "
        "seat anywhere -- the one role that must not be able to sign anything off"
    )
    assert scope.holds_anywhere(CLARIFICATION_ANSWER) is False


async def test_the_two_holders_who_really_are_unrestricted_still_are(
    app_session: AppSessionFactory,
) -> None:
    """The control. A guard against `view_any` is only useful if it did not also
    take `decide_any` and `all_departments` away -- and both of those are how the
    company-wide budget incident (`department_id IS NULL`) gets answered at all.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        engineering = await _department(db, tenant, "Entwicklung")

        # The token term: `org_admin` holds `approval:decide_any` via ALL_PERMISSIONS.
        _admin, admin_scope = await scope_for_principal(
            db, _principal(tenant, "boss", "org_admin"), upsert=True
        )
        # The row term: a CEO whose token says nothing at all.
        db.add(
            m.OrgMember(
                tenant_id=tenant,
                subject="ceo",
                subject_uuid=subject_uuid_for("ceo"),
                all_departments=True,
            )
        )
        await db.flush()
        _ceo, ceo_scope = await scope_for_principal(db, _principal(tenant, "ceo", "member"))

    for scope in (admin_scope, ceo_scope):
        assert scope.may_view(engineering) is True
        assert scope.may_decide(engineering) is True
        assert scope.may_decide(None) is True
        assert scope.holds_anywhere(APPROVAL_DECIDE) is True
