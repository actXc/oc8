"""The term a department boundary is made of, before any route uses it.

Every one of these fails today because `oc8.authz.scope` does not exist. That is
the point: until this slice there was no term in the system in which the sentence
"his department, and not the whole company's" could be true. `GET /approvals`
filtered on status alone, `POST /approvals/{id}/decision` checked a permission and
never asked whose approval it was, and a human was five claims in a JWT with
nowhere to stand.

Two properties here are load-bearing beyond their own assertions:

* **test 2** -- a seat carries four permissions and nothing else. Both source
  designs died of handing the "Head of Sales" the built-in `dept_manager` role
  (`_OPERATOR` plus nine `:manage` grants, every one of them TENANT-WIDE because
  `require_permission` structurally cannot carry a resource). His approvals list
  was scoped and nothing else was.
* **test 5** -- a scope cannot be forged. The Desk's `Decider` was a frozen
  dataclass with a public constructor, so any door written next year could
  satisfy the required `actor` keyword with `ApprovalScope(frozenset(),
  unrestricted=True)` and mypy would smile at it.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.auth.principal import Principal
from oc8.authz.permissions import (
    APPROVAL,
    APPROVAL_DECIDE,
    APPROVAL_DECIDE_ANY,
    APPROVAL_VIEW_ANY,
    CLARIFICATION_ANSWER,
    CLARIFICATION_VIEW,
    SEAT_APPROVER,
    SEAT_PERMISSIONS,
    SEAT_VIEWER,
    VIEW,
    perm,
)

# `oc8.authz.scope` is the module this slice adds. Imported at module scope on
# purpose: a missing term must fail collection loudly, not be skipped.
from oc8.authz.scope import DepartmentScope, HumanActor, scope_for_principal
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

APPROVAL_VIEW = perm(APPROVAL, VIEW)

#: The six a seat may carry, written out here rather than derived from
#: `SEAT_PERMISSIONS`, so that widening the dict cannot widen its own test. Grew
#: from four to six the day `agent:view` and `department:view` graduated into
#: the view level of both seat roles (the department-scoped-agent-authority
#: design's read side); see `tests/authz/test_seat_vocabulary.py`'s
#: `_THE_ONLY_SIX`, which pins the same fact and is the canonical home for the
#: vocabulary-only assertion this test also makes.
_THE_ONLY_FOUR = frozenset(
    {
        "approval:view",
        "approval:decide",
        "clarification:view",
        "clarification:answer",
        "agent:view",
        "department:view",
    }
)

#: Rights that are TENANT-WIDE the moment any route reads them, because no route
#: that grants them resolves a department first. Each name here is one of the
#: things a Head of Sales could do to Engineering in the designs that died.
_NEVER_FROM_A_SEAT = (
    "agent:manage",
    "department:manage",
    "run:start",
    "run:control",
    "run:view",
    "knowledge:view",
    "member:manage",
    APPROVAL_VIEW_ANY,
    APPROVAL_DECIDE_ANY,
)


def _subject_uuid(subject: str) -> uuid.UUID:
    """The same derivation as `api/v1/channels.py::_subject_id`.

    Duplicated rather than imported so this file does not depend on an API
    module; `tests/channels/test_channel_decision_is_scoped.py` asserts the two
    agree, which is where that link belongs.
    """
    try:
        return uuid.UUID(subject)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"oc8:subject:{subject}")


def _principal(
    tenant: uuid.UUID, subject: str, *, role: str = "member", kind: str = "operator"
) -> Principal:
    return Principal(subject=subject, tenant_id=tenant, role=role, kind=kind)


def _assign(target: Any, field: str, value: Any) -> None:
    """A plain attribute assignment, made through an untyped reference.

    `setattr` rather than `target.field = value` so that the assignment is
    expressed once and mypy has no opinion about it either way -- the property
    under test is what happens at RUNTIME on a frozen dataclass, and a
    `type: ignore` here would go stale in whichever direction the module is
    typed on the day somebody reads it.
    """
    setattr(target, field, value)


async def _member(
    db: Any, tenant: uuid.UUID, *, subject: str, all_departments: bool = False
) -> m.OrgMember:
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
) -> m.OrgMemberDepartment:
    row = m.OrgMemberDepartment(
        tenant_id=tenant,
        member_id=member.id,
        department_id=department_id,
        seat_role=seat_role,
    )
    db.add(row)
    await db.flush()
    return row


async def _department(db: Any, tenant: uuid.UUID, name: str) -> uuid.UUID:
    row = m.Department(tenant_id=tenant, name=name)
    db.add(row)
    await db.flush()
    return row.id


# ------------------------------------------------------------------ 1. one seat


async def test_a_seat_holds_its_permissions_only_where_it_sits(
    app_session: AppSessionFactory,
) -> None:
    """A dept_approver in Vertrieb decides in Vertrieb. That is the whole of his
    authority, and `may_decide(engineering)` is the sentence the product owner
    asked for made checkable."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = await _department(db, tenant, "Vertrieb")
        engineering = await _department(db, tenant, "Entwicklung")
        member = await _member(db, tenant, subject="hos")
        await _seat(db, tenant, member, sales, SEAT_APPROVER)

        resolved, scope = await scope_for_principal(db, _principal(tenant, "hos"))

    assert resolved is not None and resolved.id == member.id
    assert scope.may_view(sales) is True
    assert scope.may_decide(sales) is True
    assert scope.may_view(engineering) is False
    assert scope.may_decide(engineering) is False
    # `department_id IS NULL` means TENANT-WIDE (a budget incident for the whole
    # company). A seat is not the tenant, so it is not his to see or to answer.
    assert scope.may_view(None) is False
    assert scope.may_decide(None) is False
    assert scope.is_empty is False
    assert scope.viewable == frozenset({sales})


# --------------------------------------------- 2. the closed seat vocabulary


async def test_a_seat_cannot_carry_a_tenant_wide_permission(
    app_session: AppSessionFactory,
) -> None:
    """The guard against the flaw that killed both source designs.

    Asserted twice on purpose. Once against the vocabulary -- a fifth entry in
    `SEAT_PERMISSIONS` is not a configuration change, it is a claim that some
    other route resolves a department before it answers, and this is where that
    claim has to be made out loud. And once against a RESOLVED scope, because a
    resolver that read the seat_role and then unioned in the caller's role
    permissions would satisfy the first assertion and hand Engineering away.
    """
    granted = frozenset().union(*SEAT_PERMISSIONS.values())
    assert granted == _THE_ONLY_FOUR, (
        "a seat carries a right in ONE department; anything else in this set is "
        "tenant-wide the moment a route without a department term reads it"
    )
    for name in _NEVER_FROM_A_SEAT:
        assert name not in granted, name

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = await _department(db, tenant, "Vertrieb")
        member = await _member(db, tenant, subject="hos")
        await _seat(db, tenant, member, sales, SEAT_APPROVER)
        _resolved, scope = await scope_for_principal(db, _principal(tenant, "hos"))

    assert scope.holds_anywhere(APPROVAL_DECIDE) is True
    assert scope.holds_anywhere(APPROVAL_VIEW) is True
    assert scope.holds_anywhere(CLARIFICATION_ANSWER) is True
    assert scope.holds_anywhere(CLARIFICATION_VIEW) is True
    for name in _NEVER_FROM_A_SEAT:
        assert scope.holds_anywhere(name) is False, (
            f"a seat granted {name}, which no route narrows -- this is the "
            f"department boundary in a different coat"
        )


# ----------------------------------------------------------- 3. two seats


async def test_two_seats_union_and_a_viewer_seat_does_not_decide(
    app_session: AppSessionFactory,
) -> None:
    """Someone can watch one department and sign off in another. If the two
    seat roles did not differ by exactly the two acting rights there would be no
    reason for the column to exist."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = await _department(db, tenant, "Vertrieb")
        marketing = await _department(db, tenant, "Marketing")
        engineering = await _department(db, tenant, "Entwicklung")
        member = await _member(db, tenant, subject="hos")
        await _seat(db, tenant, member, sales, SEAT_APPROVER)
        await _seat(db, tenant, member, marketing, SEAT_VIEWER)

        _resolved, scope = await scope_for_principal(db, _principal(tenant, "hos"))

    assert scope.viewable == frozenset({sales, marketing})
    assert scope.may_view(sales) is True
    assert scope.may_view(marketing) is True
    assert scope.may_view(engineering) is False

    assert scope.may_decide(sales) is True
    assert scope.may_decide(marketing) is False, (
        "a viewer seat that decides is the difference between the two seat roles "
        "collapsing, and nobody would be able to explain the column afterwards"
    )
    assert scope.may_decide(engineering) is False


async def test_a_revoked_seat_stops_counting(app_session: AppSessionFactory) -> None:
    """Not in §8's list, and here because the revoke path is the one that
    releases money in the wrong direction if it is wrong: the seat row is kept
    (`revoked_at`) rather than deleted, so a resolver reading every row for the
    member -- the obvious query -- keeps a revoked approver deciding for ever."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = await _department(db, tenant, "Vertrieb")
        member = await _member(db, tenant, subject="hos")
        seat = await _seat(db, tenant, member, sales, SEAT_APPROVER)
        import datetime as dt

        seat.revoked_at = dt.datetime.now(tz=dt.UTC)
        await db.flush()

        _resolved, scope = await scope_for_principal(db, _principal(tenant, "hos"))

    assert scope.is_empty is True
    assert scope.may_view(sales) is False
    assert scope.may_decide(sales) is False


# ------------------------------------------------------- 4. nothing at all


async def test_an_unbound_caller_and_an_unknown_role_both_hold_nothing(
    app_session: AppSessionFactory,
) -> None:
    """`member` is the role an employee is minted with and it holds the empty
    set; `menber` is a typo nobody mapped. Both must come out the same way --
    fail-closed, the direction that makes `permissions_for` trustworthy."""
    tenant = uuid.uuid4()
    somewhere = uuid.uuid4()
    async with app_session(tenant) as db:
        # A member row with no seats at all: known to the tenant, standing nowhere.
        await _member(db, tenant, subject="known")

        _known, known_scope = await scope_for_principal(db, _principal(tenant, "known"))
        _typo, typo_scope = await scope_for_principal(
            db, _principal(tenant, "known", role="menber")
        )
        stranger, stranger_scope = await scope_for_principal(db, _principal(tenant, "nobody"))

    assert stranger is None, "resolving must not invent a member row unless asked to upsert"
    for scope in (known_scope, typo_scope, stranger_scope):
        assert scope.is_empty is True
        assert scope.viewable == frozenset()
        assert scope.may_view(somewhere) is False
        assert scope.may_decide(somewhere) is False
        assert scope.may_view(None) is False
        assert scope.may_decide(None) is False
        assert scope.holds_anywhere(APPROVAL_VIEW) is False
        assert scope.holds_anywhere(APPROVAL_DECIDE) is False


# ------------------------------------------------- 5. it cannot be forged


async def test_a_scope_cannot_be_constructed_outside_the_resolvers(
    app_session: AppSessionFactory,
) -> None:
    """A door written next year must not be able to satisfy `actor` with an
    all-access scope it made up.

    This is the gap both judges found in The Desk, and it is why the fields are
    private and the constructor is not public API. `dataclasses.replace` is in
    the list because it is the way round a frozen dataclass that looks innocent
    in a diff -- it calls `__init__` with the fields it did not override.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = await _department(db, tenant, "Vertrieb")
        member = await _member(db, tenant, subject="hos")
        await _seat(db, tenant, member, sales, SEAT_APPROVER)
        _resolved, real = await scope_for_principal(db, _principal(tenant, "hos"))

    # The resolver still builds one -- otherwise "construction always raises"
    # would be satisfiable by a class nobody can use.
    assert real.may_decide(sales) is True
    assert real.may_view(None) is False, "the resolver handed out unrestricted authority"

    attempts: list[tuple[str, Any]] = [
        ("positional", lambda: DepartmentScope(True, frozenset(), frozenset())),
        (
            "private keywords",
            lambda: DepartmentScope(
                _unrestricted=True, _departments=frozenset(), _decide=frozenset()
            ),
        ),
        (
            "public keywords",
            lambda: DepartmentScope(unrestricted=True, departments=frozenset(), decide=frozenset()),
        ),
        ("no arguments", lambda: DepartmentScope()),
        ("dataclasses.replace", lambda: dataclasses.replace(real, _unrestricted=True)),
    ]
    for how, build in attempts:
        # B017 (blind `Exception`) is deliberate here and not laziness: the
        # property is "it refuses", and pinning the exception TYPE would let a
        # future implementation satisfy the letter of this test with a different
        # refusal while some fourth spelling quietly succeeded.
        with pytest.raises(Exception):  # noqa: B017
            forged = build()
            # `pytest.fail` raises an OutcomeException, which derives from
            # BaseException -- so `pytest.raises(Exception)` does NOT swallow it
            # and a successful forgery is reported as the failure it is.
            pytest.fail(f"{how} forged a DepartmentScope: {forged!r}")

    # And it is frozen, so the one that IS legitimate cannot be widened in place.
    with pytest.raises(Exception):  # noqa: B017
        _assign(real, "_unrestricted", True)
        pytest.fail("a resolved scope was mutated into an unrestricted one")


# --------------------------------------- 6. unrestricted is a boolean


async def test_an_org_admin_is_unrestricted_without_enumerating_departments(
    app_session: AppSessionFactory,
) -> None:
    """A department created AFTER the scope was resolved is inside it by
    construction.

    `unrestricted` is a boolean and not a set holding every department id, and
    this is the test that says why: a set has to be re-remembered every time a
    department is created, and the one time it is forgotten a CEO silently stops
    seeing a department's approvals -- silently, because nothing errors.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        existing = await _department(db, tenant, "Vertrieb")
        _resolved, scope = await scope_for_principal(
            db, _principal(tenant, "boss", role="org_admin"), upsert=True
        )
        # Resolved BEFORE this one exists.
        latecomer = await _department(db, tenant, "Neu gegründet")

    assert scope.is_empty is False
    assert scope.may_view(existing) is True
    assert scope.may_view(latecomer) is True
    assert scope.may_decide(latecomer) is True
    # The tenant-wide budget incident (`department_id IS NULL`) belongs to
    # exactly this caller and to nobody with a seat.
    assert scope.may_view(None) is True
    assert scope.may_decide(None) is True


async def test_the_row_term_makes_a_ceo_unrestricted_without_a_token(
    app_session: AppSessionFactory,
) -> None:
    """Not in §8's list. The messenger door has no token, so `all_departments`
    on the row is the ONLY unrestricted term available there -- and if the
    resolver reads it only from the role, a CEO deciding from Telegram silently
    becomes a seatless nobody."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        somewhere = await _department(db, tenant, "Vertrieb")
        await _member(db, tenant, subject="ceo", all_departments=True)
        _resolved, scope = await scope_for_principal(db, _principal(tenant, "ceo"))

    assert scope.may_view(somewhere) is True
    assert scope.may_decide(somewhere) is True
    assert scope.may_view(None) is True


# --------------------------------------------- 7. only an operator resolves


async def test_scope_refuses_a_non_operator_principal(app_session: AppSessionFactory) -> None:
    """An agent token and a plugin token have a `sub`, and a resolver that
    looked people up by `sub` alone would happily find a member row for one.

    Refused by the `kind` rather than by hoping `role="plugin"` stays unknown:
    an agent token that resolves to a member is the container approving its own
    >3000 EUR call, which is exactly what `deny_agent_principals` exists to stop
    and is a hole that has already been reachable once on this system.

    `PermissionError` and not a bare `assert`: `python -O` strips asserts, and
    an authorization check that a compiler flag removes is not a check.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _member(db, tenant, subject="hos")

        for kind in ("agent", "plugin"):
            with pytest.raises(PermissionError):
                await scope_for_principal(db, _principal(tenant, "hos", kind=kind))
            with pytest.raises(PermissionError):
                await scope_for_principal(db, _principal(tenant, "hos", kind=kind), upsert=True)


# ------------------------------------------------------ the house rule


async def test_resolving_a_scope_does_not_commit_the_request_transaction(
    app_session: AppSessionFactory,
) -> None:
    """Not in §8's list, and here because this repository has been bitten by it
    repeatedly: a COMMIT inside a `tenant_session` unbinds `app.tenant_id`, and
    every statement after it in that request either sees nothing or fails
    casting `''` to uuid.

    `require_departmental` resolves the scope with `upsert=True` on the REQUEST
    session, before the route body runs. If the upsert commits to make itself
    durable, the whole request after the gate is unbound -- and the symptom is an
    empty approvals list, not an error.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = await _department(db, tenant, "Vertrieb")

        member, scope = await scope_for_principal(db, _principal(tenant, "first-time"), upsert=True)
        assert member is not None, "upsert=True must mint the row `decided_by` points at"
        assert scope.is_empty is True

        # Still bound: if the resolver committed, RLS now compares against '' and
        # this either raises or comes back empty.
        rows = (await db.execute(select(m.Department).where(m.Department.id == sales))).all()
        assert len(rows) == 1, "the tenant GUC was unbound by a commit inside the resolver"

        # Idempotent, and still no commit the second time round.
        again, _scope = await scope_for_principal(db, _principal(tenant, "first-time"), upsert=True)
        assert again is not None and again.id == member.id


async def test_a_human_actor_carries_the_scope_it_was_resolved_with(
    app_session: AppSessionFactory,
) -> None:
    """Not in §8's list. `HumanActor` is what every funnel takes, so a version
    that let the scope be swapped after resolution would put the door back where
    it was."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = await _department(db, tenant, "Vertrieb")
        member = await _member(db, tenant, subject="hos")
        await _seat(db, tenant, member, sales, SEAT_APPROVER)
        principal = _principal(tenant, "hos")
        resolved, scope = await scope_for_principal(db, principal)

    assert resolved is not None
    actor = HumanActor(principal=principal, member=resolved, scope=scope)
    assert actor.scope.may_decide(sales) is True
    assert actor.via == "inbox"
    with pytest.raises(Exception):  # noqa: B017
        _assign(actor, "scope", scope)
        pytest.fail("an actor's scope can be replaced after the gate resolved it")
