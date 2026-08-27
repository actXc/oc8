"""The resolver's own edges -- the ones `test_department_scope.py` does not reach.

That file pins the TERM (§8.1-7): what a seat grants, that a scope cannot be
forged, that the resolver does not commit. This one pins the four places the
RESOLUTION itself can be subtly wrong while every one of those tests still
passes:

* an unrestricted scope answering `holds_anywhere` for a permission no seat
  carries -- the gate reads exactly that method;
* the upsert meeting a soft-deleted person, i.e. somebody who was offboarded;
* the upsert meeting itself, from two first requests at once, which is the whole
  reason it is `ON CONFLICT DO NOTHING` and not `db.add()`;
* `scope_for_binding`, the messenger door, which has no token and is reached by
  no test in `tests/authz/`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.auth.principal import Principal
from oc8.authz.permissions import (
    APPROVAL,
    APPROVAL_DECIDE,
    CLARIFICATION_ANSWER,
    CLARIFICATION_VIEW,
    SEAT_APPROVER,
    SEAT_VIEWER,
    VIEW,
    perm,
)
from oc8.authz.scope import scope_for_binding, scope_for_principal, subject_uuid_for
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

APPROVAL_VIEW = perm(APPROVAL, VIEW)


def _principal(tenant: uuid.UUID, subject: str, *, role: str = "member") -> Principal:
    return Principal(subject=subject, tenant_id=tenant, role=role, kind="operator")


async def _department(db: Any, tenant: uuid.UUID, name: str) -> uuid.UUID:
    row = m.Department(tenant_id=tenant, name=name)
    db.add(row)
    await db.flush()
    return row.id


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


# ------------------------------------------------- the gate's actual question


async def test_an_unrestricted_scope_holds_nothing_outside_the_seat_vocabulary(
    app_session: AppSessionFactory,
) -> None:
    """`require_departmental` admits on `role_has(...) or scope.holds_anywhere(...)`,
    so `holds_anywhere` is a grant, not a query helper.

    An unrestricted scope answering "yes" to anything asked of it is the failure
    here: `all_departments` is a ROW an admin writes, it carries no permission of
    its own, and the day somebody writes `require_departmental(RUN_CONTROL)` a
    permissive answer hands a seatless CEO a tenant-wide right through a gate
    whose name says departmental. The four it may answer for are the four in
    `SEAT_PERMISSIONS`, and nothing else -- including the two `_any` permissions,
    which are the token term and must never come back out of the row term.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(
            m.OrgMember(
                tenant_id=tenant,
                subject="ceo",
                subject_uuid=subject_uuid_for("ceo"),
                all_departments=True,
            )
        )
        await db.flush()
        _member, scope = await scope_for_principal(db, _principal(tenant, "ceo"))

    assert scope.is_unrestricted is True
    for held in (APPROVAL_VIEW, APPROVAL_DECIDE, CLARIFICATION_VIEW, CLARIFICATION_ANSWER):
        assert scope.holds_anywhere(held) is True, held
    for never in (
        "run:control",
        "run:start",
        "agent:manage",
        "member:manage",
        "knowledge:view",
        "approval:view_any",
        "approval:decide_any",
    ):
        assert scope.holds_anywhere(never) is False, (
            f"an unrestricted scope granted {never}; `all_departments` says WHERE, never WHAT"
        )


async def test_a_viewer_seat_is_refused_at_the_door_it_may_not_pass(
    app_session: AppSessionFactory,
) -> None:
    """The door asks `holds_anywhere`, not `may_decide`. A dept_viewer holds
    `approval:decide` in NO department, so he must be turned away before any row
    is loaded -- if he is admitted and only narrowed afterwards, an empty result
    reads as "nothing to do" rather than "not your job", and the difference is the
    whole of the two empty states §7 insists on."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = await _department(db, tenant, "Vertrieb")
        member = m.OrgMember(
            tenant_id=tenant, subject="watcher", subject_uuid=subject_uuid_for("watcher")
        )
        db.add(member)
        await db.flush()
        await _seat(db, tenant, member, sales, SEAT_VIEWER)
        _resolved, scope = await scope_for_principal(db, _principal(tenant, "watcher"))

    assert scope.holds_anywhere(APPROVAL_VIEW) is True
    assert scope.holds_anywhere(CLARIFICATION_VIEW) is True
    assert scope.holds_anywhere(APPROVAL_DECIDE) is False
    assert scope.holds_anywhere(CLARIFICATION_ANSWER) is False
    assert scope.is_empty is False
    assert scope.viewable == frozenset({sales})


# ------------------------------------------------------------- the upsert


async def test_re_enrolling_a_removed_person_does_not_resurrect_their_authority(
    app_session: AppSessionFactory,
) -> None:
    """Offboarding is `deleted_at`, and the partial unique index is what lets the
    same subject be enrolled again.

    The failure this guards is the one that matters most in this file: an upsert
    written as "find any row for this subject" would hand a removed CEO his
    `all_departments = true` back on his next request, and nothing would error.
    A removed person is a stranger; the row that comes back is a NEW one holding
    nothing.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        gone = m.OrgMember(
            tenant_id=tenant,
            subject="ceo",
            subject_uuid=subject_uuid_for("ceo"),
            all_departments=True,
            deleted_at=dt.datetime.now(tz=dt.UTC),
        )
        db.add(gone)
        await db.flush()

        # Read-only first: a removed person is not resolvable at all.
        stranger, stranger_scope = await scope_for_principal(db, _principal(tenant, "ceo"))
        assert stranger is None
        assert stranger_scope.is_empty is True

        minted, scope = await scope_for_principal(db, _principal(tenant, "ceo"), upsert=True)

    assert minted is not None
    assert minted.id != gone.id, "the upsert resurrected a soft-deleted member row"
    assert minted.all_departments is False
    assert scope.is_unrestricted is False
    assert scope.is_empty is True


async def test_the_minted_subject_uuid_is_the_one_the_messenger_door_looks_up_by(
    app_session: AppSessionFactory,
) -> None:
    """`org_member.subject_uuid` earns its column only if it is the SAME value
    `api/v1/channels.py::_subject_id` writes into `ApprovalChannelBinding.user_id`.

    If the upsert derives it any other way the two never meet: linking a phone
    would either collide with `uq_org_member_subject_uuid` or create a second row
    for one human, and the person behind a binding becomes unresolvable -- which
    means every messenger decision is refused, silently, for the population
    piloting the feature.
    """
    from oc8.api.v1.channels import _subject_id

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        # A name (dev logins) and a subject that already IS a uuid (Keycloak).
        for subject in ("lena.krueger", str(uuid.uuid4())):
            principal = _principal(tenant, subject)
            minted, _scope = await scope_for_principal(db, principal, upsert=True)
            assert minted is not None
            assert minted.subject == subject, "the token's `sub` is stored verbatim"
            assert minted.subject_uuid == _subject_id(principal)
            assert minted.subject_uuid == subject_uuid_for(subject)


async def test_two_first_requests_from_the_same_new_person_agree_on_one_member(
    app_session: AppSessionFactory,
) -> None:
    """Two tabs, two transactions, one person who has never been seen before.

    This is why the upsert is `INSERT ... ON CONFLICT DO NOTHING` and not
    `db.add()` + flush: a flush that raises IntegrityError leaves the session
    needing rollback, so the caller's next statement dies with
    PendingRollbackError and a harmless race becomes a failed request. It is also
    why `DO NOTHING` and not `DO UPDATE` -- `DO UPDATE` takes a row lock even when
    its WHERE is false, so the second request would wait on the FIRST REQUEST'S
    ENTIRE ROUTE BODY, this resolver being the gate that runs before it.

    The second transaction is made to arrive while the first is still open, which
    is the only ordering that exercises the conflict at all.
    """
    tenant = uuid.uuid4()
    subject = f"new-hire-{uuid.uuid4()}"
    first_is_in = asyncio.Event()

    async def _first() -> uuid.UUID:
        async with app_session(tenant) as db:
            member, _scope = await scope_for_principal(db, _principal(tenant, subject), upsert=True)
            assert member is not None
            first_is_in.set()
            # Still uncommitted while the second request runs into it.
            await asyncio.sleep(0.2)
            return member.id

    async def _second() -> uuid.UUID:
        await first_is_in.wait()
        async with app_session(tenant) as db:
            member, _scope = await scope_for_principal(db, _principal(tenant, subject), upsert=True)
            assert member is not None
            return member.id

    one, two = await asyncio.wait_for(asyncio.gather(_first(), _second()), timeout=30)
    assert one == two, "two first requests minted two people; `decided_by` now names either"

    # And exactly one row exists, not two.
    async with app_session(tenant) as db:
        rows = (
            (await db.execute(select(m.OrgMember).where(m.OrgMember.subject == subject)))
            .scalars()
            .all()
        )
    assert len(rows) == 1


# --------------------------------------------------------- the messenger door


async def _binding(
    db: Any, tenant: uuid.UUID, *, member: m.OrgMember | None, external_id: str = "chat-1"
) -> m.ApprovalChannelBinding:
    row = m.ApprovalChannelBinding(
        tenant_id=tenant,
        channel="fake",
        user_id=member.subject_uuid if member is not None else uuid.uuid4(),
        member_id=member.id if member is not None else None,
        external_id=external_id,
    )
    db.add(row)
    await db.flush()
    return row


async def test_a_binding_carries_only_its_own_persons_seats(
    app_session: AppSessionFactory,
) -> None:
    """The channel path has no token, so `all_departments` on the row is the ONLY
    unrestricted term available there. A resolver that read the token term would
    make a CEO deciding from Telegram a seatless nobody; one that assumed the row
    term for every binding would make every bound phone the CEO."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = await _department(db, tenant, "Vertrieb")
        engineering = await _department(db, tenant, "Entwicklung")

        hos = m.OrgMember(tenant_id=tenant, subject="hos", subject_uuid=subject_uuid_for("hos"))
        ceo = m.OrgMember(
            tenant_id=tenant,
            subject="ceo",
            subject_uuid=subject_uuid_for("ceo"),
            all_departments=True,
        )
        db.add_all([hos, ceo])
        await db.flush()
        await _seat(db, tenant, hos, sales, SEAT_APPROVER)

        who, hos_scope = await scope_for_binding(db, await _binding(db, tenant, member=hos))
        _ceo, ceo_scope = await scope_for_binding(
            db, await _binding(db, tenant, member=ceo, external_id="chat-2")
        )

    assert who is not None and who.id == hos.id
    assert hos_scope.may_decide(sales) is True
    assert hos_scope.may_decide(engineering) is False
    assert hos_scope.may_decide(None) is False, "a phone decided the company-wide incident"

    assert ceo_scope.is_unrestricted is True
    assert ceo_scope.may_decide(engineering) is True
    assert ceo_scope.may_decide(None) is True


async def test_a_binding_with_no_member_and_one_whose_person_is_gone_both_hold_nothing(
    app_session: AppSessionFactory,
) -> None:
    """Two rows the door must treat identically, and neither is an error here --
    `dispatch.decision_from` turns both into the same one-sentence refusal it
    gives an unknown sender, because an error that tells them apart makes the bot
    a probe for which approvals exist.

    `member_id IS NULL` is every binding issued before migration 0046, which
    deliberately grandfathers none of them. The removed-person case is
    offboarding: the seat rows are still there, and a resolver that read them
    without checking `deleted_at` would let a leaver keep deciding from a phone
    nobody remembered to unbind.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = await _department(db, tenant, "Vertrieb")
        leaver = m.OrgMember(
            tenant_id=tenant, subject="leaver", subject_uuid=subject_uuid_for("leaver")
        )
        db.add(leaver)
        await db.flush()
        await _seat(db, tenant, leaver, sales, SEAT_APPROVER)
        legacy = await _binding(db, tenant, member=None)
        theirs = await _binding(db, tenant, member=leaver, external_id="chat-2")

        legacy_member, legacy_scope = await scope_for_binding(db, legacy)

        leaver.deleted_at = dt.datetime.now(tz=dt.UTC)
        await db.flush()
        gone_member, gone_scope = await scope_for_binding(db, theirs)

    for who, scope in ((legacy_member, legacy_scope), (gone_member, gone_scope)):
        assert who is None
        assert scope.is_empty is True
        assert scope.is_unrestricted is False
        assert scope.may_view(sales) is False
        assert scope.may_decide(sales) is False
        assert scope.may_decide(None) is False


async def test_the_resolvers_run_on_the_shared_test_database() -> None:
    """A self-check, so that "every scope test passed" cannot mean "they were all
    skipped against a database that was never there"."""
    assert os.environ.get("OC8_DATABASE_URL"), "these tests assert nothing without a real Postgres"
