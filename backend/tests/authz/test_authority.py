"""One resolver for "what may this person do", and the floor it never drops below.

`authz/authority.py` is the only place a database row is allowed to answer that
question. Everything in this file is about the two directions it can be wrong in,
and they are not symmetrical:

* **It can fail to narrow.** An administrator assigns "Praktikant" and the person
  keeps every one of the 52 permissions their token carries. That is what a UNION
  would do -- and it is not hypothetical, it is what every human on every live
  tenant would experience, because `POST /auth/setup` mints `org_admin` and
  nothing ever passes `role=`. The demotion lever is the feature; a resolver that
  cannot demote has shipped a screen and nothing behind it.
* **It can fail to widen back.** A tenant that has configured NOTHING -- no role
  rows, no assignments, which is every tenant the day this lands -- must resolve
  byte-identically to `permissions_for(token.role)`. `permissions.py:18-24` is
  explicit that an authorization layer whose grants come from rows locks every
  operator out the day the rows are missing. So a missing row never subtracts:
  no member row and a NULL `role_id` both mean "the token decides".

Between those two, one rule: **a row an administrator deliberately wrote decides;
the absence of a row decides nothing.**

The third direction is the one that has no symmetric partner: a row that is
present but UNUSABLE -- soft-deleted, agent-kind, dangling -- must resolve to the
empty set and must NOT fall back to the token floor. Falling back would mean
deleting a role silently restored forty demoted people to `org_admin`, which is
the one failure nobody would see until it was audited.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from oc8 import models as m
from oc8.auth import Principal
from oc8.auth.principal import PrincipalKind
from oc8.authz.permissions import (
    ALL_PERMISSIONS,
    APPROVAL,
    APPROVAL_DECIDE,
    APPROVAL_DECIDE_ANY,
    APPROVAL_VIEW_ANY,
    AUDIT,
    CLARIFICATION_ANSWER,
    CLARIFICATION_VIEW,
    DELEGATABLE_PERMISSIONS,
    MANAGE,
    OPERATOR,
    ORG_ADMIN,
    PLUGIN,
    SEAT_APPROVER,
    TOOL_SEND,
    VIEW,
    perm,
    permissions_for,
)
from oc8.authz.scope import scope_for_principal, subject_uuid_for
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def authority_for_principal(request: Request, db: AsyncSession, principal: Principal) -> Any:
    """`oc8.authz.authority.authority_for_principal`, imported at CALL time.

    A module-level `from oc8.authz.authority import ...` would be a COLLECTION
    error while that module does not exist yet, and pytest aborts the whole
    session on one of those -- so every test in the repository would be
    unrunnable until this slice's resolver is written. Imported here, the same
    absence is a failure of exactly the tests that need it, which is what red is
    supposed to look like.

    Delete this shim and import normally once `authz/authority.py` exists.
    """
    from oc8.authz.authority import authority_for_principal as _resolve

    return await _resolve(request, db, principal)


async def role_permissions(db: AsyncSession, role_id: uuid.UUID) -> frozenset[str]:
    """`oc8.authz.authority.role_permissions`; see above."""
    from oc8.authz.authority import role_permissions as _resolve

    return frozenset(await _resolve(db, role_id))


APPROVAL_VIEW = perm(APPROVAL, VIEW)
AUDIT_VIEW = perm(AUDIT, VIEW)
PLUGIN_MANAGE = perm(PLUGIN, MANAGE)

#: A tenant role an IT admin would plausibly write: the four things a person who
#: signs off offers and answers questions needs, and nothing else.
FREIGABE = frozenset({APPROVAL_VIEW, APPROVAL_DECIDE, CLARIFICATION_VIEW, CLARIFICATION_ANSWER})


def _principal(
    tenant: uuid.UUID,
    *,
    subject: str = "anna",
    role: str = ORG_ADMIN,
    kind: PrincipalKind = "operator",
) -> Principal:
    return Principal(subject=subject, tenant_id=tenant, role=role, kind=kind)


def _request() -> Request:
    """A bare ASGI scope. `request.state` is where the resolver memoises, and
    Starlette builds it lazily from the scope, so nothing else is needed."""
    return Request(
        {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}
    )


@contextmanager
def _statements() -> Iterator[list[str]]:
    """Every SQL statement issued while this is open.

    On the `Engine` class rather than on one instance: `app_session` builds a
    throwaway engine per call, so an instance-level listener would have nothing
    to attach to.
    """
    seen: list[str] = []

    def _record(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", _record)


async def _role(
    db: AsyncSession,
    tenant: uuid.UUID,
    name: str,
    permissions: frozenset[str] | set[str] = frozenset(),
    *,
    builtin: bool = False,
    kind: str = "human",
    deleted: bool = False,
) -> uuid.UUID:
    role = m.Role(tenant_id=tenant, name=name, builtin=builtin, kind=kind)
    db.add(role)
    await db.flush()
    for permission in sorted(permissions):
        db.add(m.RolePermission(tenant_id=tenant, role_id=role.id, permission=permission))
    if deleted:
        role.deleted_at = dt.datetime.now(tz=dt.UTC)
    await db.flush()
    return role.id


async def _member(
    db: AsyncSession, tenant: uuid.UUID, subject: str, *, role_id: uuid.UUID | None = None
) -> m.OrgMember:
    member = m.OrgMember(
        tenant_id=tenant,
        subject=subject,
        subject_uuid=subject_uuid_for(subject),
        display_name=subject,
        role_id=role_id,
    )
    db.add(member)
    await db.flush()
    return member


# --------------------------------------------------------------- the token floor


async def test_no_member_row_resolves_to_the_token_floor(app_session: AppSessionFactory) -> None:
    """The doctrine at `permissions.py:18-24`, as an executable assertion.

    An `org_admin` the system has never seen holds all 52. Not "most of them",
    not "the ones a default role grants" -- the identical frozenset the code
    table returns today, because on the day this lands that is every human on
    every tenant and any difference at all is an outage.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        authority = await authority_for_principal(_request(), db, _principal(tenant))

    assert authority.tenant_wide == permissions_for(ORG_ADMIN)
    assert authority.tenant_wide == ALL_PERMISSIONS
    assert authority.source == "token"
    assert authority.member is None


async def test_a_member_row_with_no_role_resolves_to_the_token_floor(
    app_session: AppSessionFactory,
) -> None:
    """The seat slice mints a member row on everybody's first request, so by the
    time this feature exists nearly every human HAS a row. That upsert must
    subtract nothing: NULL is "the token decides", not "no permissions"."""
    tenant = uuid.uuid4()
    subject = f"anna-{uuid.uuid4()}"
    async with app_session(tenant) as db:
        member = await _member(db, tenant, subject)
        assert member.role_id is None
        authority = await authority_for_principal(
            _request(), db, _principal(tenant, subject=subject, role=OPERATOR)
        )

    assert authority.tenant_wide == permissions_for(OPERATOR)
    assert authority.source == "token"
    assert authority.member is not None and authority.member.id == member.id


async def test_an_unknown_role_still_holds_nothing(app_session: AppSessionFactory) -> None:
    """A regression pin, and it passes today -- that is the point.

    `menber` is a typo in an IdP mapping. It resolved to the empty set before
    this slice and must still, because the new resolver's fallback is exactly
    that code path. A resolver that "helpfully" defaulted an unrecognised role to
    something would admit whoever mistyped it.
    """
    assert permissions_for("menber") == frozenset()

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        authority = await authority_for_principal(_request(), db, _principal(tenant, role="menber"))

    assert authority.tenant_wide == frozenset()
    assert authority.unrestricted is False
    assert authority.source == "token"


# ------------------------------------------------------------- the demotion lever


async def test_an_assigned_role_replaces_the_token_floor(app_session: AppSessionFactory) -> None:
    """The feature, and the thing a UNION cannot do.

    The token says `org_admin`, because that is what `POST /auth/setup`
    mints and no caller has ever passed anything else. The administrator says
    "Freigabe Vertrieb". The administrator wins -- otherwise assigning a role is
    a no-op the screen reports as a success, which was the fatal flaw of the
    draft that proposed a union.
    """
    tenant = uuid.uuid4()
    subject = f"anna-{uuid.uuid4()}"
    async with app_session(tenant) as db:
        role_id = await _role(db, tenant, "Freigabe Vertrieb", FREIGABE)
        await _member(db, tenant, subject, role_id=role_id)
        authority = await authority_for_principal(
            _request(), db, _principal(tenant, subject=subject, role=ORG_ADMIN)
        )

    assert authority.tenant_wide == FREIGABE
    assert authority.source == "assigned"
    assert PLUGIN_MANAGE not in authority.tenant_wide
    assert authority.tenant_wide < permissions_for(ORG_ADMIN), (
        "the assignment did not narrow anything; a role that cannot demote is a screen"
    )


async def test_an_assigned_role_cannot_keep_the_unrestricted_flag_the_token_gave(
    app_session: AppSessionFactory,
) -> None:
    """`unrestricted` is computed from the RESOLVED set, never from the token.

    `approval:view_any` and `approval:decide_any` are the words "in every
    department". If the flag kept being read off `role_has(principal.role, ...)`
    after this slice, a demoted administrator would still see and decide every
    department's approvals -- the demotion would apply to every screen except the
    one that releases money.
    """
    tenant = uuid.uuid4()
    subject = f"anna-{uuid.uuid4()}"
    async with app_session(tenant) as db:
        role_id = await _role(db, tenant, "Freigabe DACH", FREIGABE)
        await _member(db, tenant, subject, role_id=role_id)
        authority = await authority_for_principal(
            _request(), db, _principal(tenant, subject=subject, role=ORG_ADMIN)
        )

    assert APPROVAL_VIEW_ANY not in authority.tenant_wide
    assert APPROVAL_DECIDE_ANY not in authority.tenant_wide
    assert authority.unrestricted is False, (
        "a demoted administrator is still unrestricted; the flag is being read "
        "off the token rather than off the resolved set"
    )


async def test_an_assigned_role_does_not_take_a_seat_away(
    app_session: AppSessionFactory,
) -> None:
    """The override replaces the TOKEN floor. It is not a replacement of the
    person.

    Role says WHAT, seat says WHERE, and they are different rows written by
    different acts. An admin who assigns "Mitarbeiter" -- which holds nothing
    tenant-wide -- to somebody with a `dept_approver` seat in Vertrieb has said
    "no company-wide authority", not "no authority at all". If the resolver
    dropped the seats, every promotion in the 500-person tenant would silently
    empty the promoted person's workspace.
    """
    tenant = uuid.uuid4()
    subject = f"anna-{uuid.uuid4()}"
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb")
        db.add(dept)
        await db.flush()
        role_id = await _role(db, tenant, "Mitarbeiter", frozenset())
        member = await _member(db, tenant, subject, role_id=role_id)
        db.add(
            m.OrgMemberDepartment(
                tenant_id=tenant,
                member_id=member.id,
                department_id=dept.id,
                seat_role=SEAT_APPROVER,
            )
        )
        await db.flush()
        principal = _principal(tenant, subject=subject, role=ORG_ADMIN)
        authority = await authority_for_principal(_request(), db, principal)
        # Asked of the SCOPE, which is the one seat reader on the authorization
        # path and the object `require_departmental` actually admits on. The
        # resolver deliberately carries no seat map of its own.
        _seated, scope = await scope_for_principal(db, principal)
        dept_id = dept.id

    assert authority.tenant_wide == frozenset()
    assert scope.holds_anywhere(APPROVAL_DECIDE) is True
    assert scope.may_decide(dept_id) is True
    assert scope.may_view(dept_id) is True


# ------------------------------------------------------------------- the seam


async def test_permissions_for_still_cannot_see_a_tenant_role(
    app_session: AppSessionFactory,
) -> None:
    """The seam guard.

    `permissions_for` is called by `tool_rights_for_role`, which is called by
    `pdp.agent_tool_rights`. Teaching it to read rows would hand the AGENT path
    tenant-authored grants for free. It stays synchronous, tenant-less,
    session-less and code-only -- so a role with 21 permissions in the database
    is invisible to it, by name and forever.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _role(db, tenant, "Alles Lesbare", DELEGATABLE_PERMISSIONS)

    assert permissions_for("Alles Lesbare") == frozenset()


async def test_a_row_on_a_builtin_role_cannot_widen_it(app_session: AppSessionFactory) -> None:
    """`builtin=True` means the grants come from CODE, so `role_permission` rows
    written against one are inert by design.

    `audit:view` is the probe on purpose: it is delegatable, and `operator` is
    deliberately NOT given it (`_NOT_VIEWABLE_BY_DEFAULT`), so a resolver that
    read the rows would show the widening rather than merely failing to notice
    it. Somebody with psql must not be able to give `operator` the audit trail.
    """
    tenant = uuid.uuid4()
    subject = f"anna-{uuid.uuid4()}"
    async with app_session(tenant) as db:
        role_id = await _role(db, tenant, OPERATOR, {AUDIT_VIEW}, builtin=True)
        await _member(db, tenant, subject, role_id=role_id)
        resolved = await role_permissions(db, role_id)
        authority = await authority_for_principal(
            _request(), db, _principal(tenant, subject=subject, role=OPERATOR)
        )

    assert AUDIT_VIEW not in permissions_for(OPERATOR), "the probe is no longer a widening"
    assert resolved == permissions_for(OPERATOR)
    assert AUDIT_VIEW not in authority.tenant_wide


async def test_a_grant_outside_the_delegatable_set_is_inert(
    app_session: AppSessionFactory,
) -> None:
    """Asserted at the RESOLVER, not at the endpoint.

    `POST /roles` refuses `plugin:manage` with a 422 -- but a row can also arrive
    by restore, by psql, or from an importer written next year, and none of those
    goes through the endpoint. The intersection with `DELEGATABLE_PERMISSIONS`
    runs on every read, which is also what makes a permission graduating OUT of
    that set go inert everywhere on the next request with no backfill.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        role_id = await _role(
            db, tenant, "Zu viel", {APPROVAL_VIEW, PLUGIN_MANAGE, TOOL_SEND, "run:view"}
        )
        resolved = await role_permissions(db, role_id)

    assert resolved == {APPROVAL_VIEW}
    assert PLUGIN_MANAGE not in resolved
    assert TOOL_SEND not in resolved, "a person resolved one of the agent's tool rights"
    assert "run:view" not in resolved, "a not-yet-delegatable read resolved anyway"


@pytest.mark.parametrize(
    ("name", "kwargs", "why"),
    [
        ("Gelöscht", {"deleted": True}, "a soft-deleted role"),
        ("agent_default", {"kind": "agent"}, "an agent-kind role"),
    ],
)
async def test_a_soft_deleted_or_agent_kind_role_grants_a_person_nothing(
    app_session: AppSessionFactory, name: str, kwargs: dict[str, Any], why: str
) -> None:
    """Both resolve to the empty set, and -- the half that is easy to get wrong --
    NOT to the token floor.

    `SoftDeleteMixin` adds no query filter, so the `deleted_at` check is the only
    thing standing between a deleted role and a role that still grants. And a
    fallback to the token here would mean deleting a role silently restored every
    holder to `org_admin`: forty people re-promoted by an act whose whole purpose
    was to take authority away.
    """
    tenant = uuid.uuid4()
    subject = f"anna-{uuid.uuid4()}"
    async with app_session(tenant) as db:
        role_id = await _role(db, tenant, name, FREIGABE, **kwargs)
        await _member(db, tenant, subject, role_id=role_id)
        resolved = await role_permissions(db, role_id)
        authority = await authority_for_principal(
            _request(), db, _principal(tenant, subject=subject, role=ORG_ADMIN)
        )

    assert resolved == frozenset(), why
    assert authority.tenant_wide == frozenset(), (
        f"{why} fell back to the token floor, so the holder is an org_admin again"
    )


async def test_a_non_operator_principal_never_resolves_a_role(
    app_session: AppSessionFactory,
) -> None:
    """By the KIND, asserted -- not inferred from `role="plugin"` staying unknown.

    A plugin token (`hooks/executor.py:50`) and an agent token both carry a `sub`.
    A resolver that looked people up by subject alone would happily find a member
    row for one, which is the container reading a person's authority. The empty
    answer must come from the assertion, so it survives somebody adding `plugin`
    to `BUILTIN_ROLE_PERMISSIONS` for an unrelated reason.
    """
    tenant = uuid.uuid4()
    subject = f"plugin-{uuid.uuid4()}"
    async with app_session(tenant) as db:
        role_id = await _role(db, tenant, "Freigabe", FREIGABE)
        await _member(db, tenant, subject, role_id=role_id)

        with _statements() as issued:
            authority = await authority_for_principal(
                _request(),
                db,
                _principal(tenant, subject=subject, role=ORG_ADMIN, kind="plugin"),
            )

    assert authority.tenant_wide == frozenset()
    assert authority.member is None
    assert authority.unrestricted is False
    assert not [s for s in issued if "org_member" in s], (
        "a non-operator principal was looked up as a person before being refused"
    )


async def test_the_gate_costs_one_statement_and_an_assignment_costs_two(
    app_session: AppSessionFactory,
) -> None:
    """What the gate costs, counted as STATEMENTS and not as mentions of a table.

    The design's cost argument is "one indexed single-row SELECT per request, and
    no new session anywhere". Two things can make that false and only one of them
    used to be visible here.

    * **Memoisation.** Without the memo on `request.state`, a route carrying
      `require_permission` and `require_departmental` pays twice. Counted against
      the SAME `Request`, which is what a request is; a second `Request` must pay
      again, or "revoking a role takes effect on the next request" stops being
      true.
    * **The shape of the read.** This assertion used to filter the log down to
      statements containing `"org_member"`, so it counted 1 while the resolver
      was in fact issuing three -- a three-table LEFT JOIN over the seat tables,
      a role fetch, and a grant fetch -- and it would have gone on counting 1 if
      a fourth had been added. Every statement is counted now, and the numbers
      are named: **1** for the floor (every caller on every tenant that has
      configured nothing) and **2** for a caller with an assignment.

    The join is asserted away explicitly as well. A seat map on the `Authority`
    was read by nothing in `src/` and cost that join on all 110 permission-gated
    routes; if it comes back, the count goes to 2 for the floor and this fails
    with the reason written down.
    """
    tenant = uuid.uuid4()
    floor_subject = f"floor-{uuid.uuid4()}"
    subject = f"anna-{uuid.uuid4()}"
    async with app_session(tenant) as db:
        role_id = await _role(db, tenant, "Freigabe", FREIGABE)
        await _member(db, tenant, subject, role_id=role_id)
        principal = _principal(tenant, subject=subject, role=ORG_ADMIN)
        floor = _principal(tenant, subject=floor_subject, role=ORG_ADMIN)

        with _statements() as floor_cost:
            await authority_for_principal(_request(), db, floor)

        request = _request()
        with _statements() as issued:
            first = await authority_for_principal(request, db, principal)
            second = await authority_for_principal(request, db, principal)

        with _statements() as next_request:
            await authority_for_principal(_request(), db, principal)

    assert first is second, "the second gate re-resolved instead of reading the memo"
    assert len(floor_cost) == 1, (
        f"a caller with no assignment cost {len(floor_cost)} statements, not one: {floor_cost}"
    )
    assert len(issued) == 2, (
        f"a caller with an assignment cost {len(issued)} statements, not two: {issued}"
    )
    assert len(next_request) == 2, (
        "a second request reused the first request's answer; a revocation would "
        "not land until the process restarted"
    )
    # `org_member_department` and `FROM department`, not the bare word: the member
    # row carries an `all_departments` COLUMN, and a substring match on
    # "department" would call the single-row SELECT a seat read and pass for the
    # wrong reason.
    joined = [
        s for s in floor_cost + issued if "org_member_department" in s or "FROM department" in s
    ]
    assert not joined, f"the resolver is reading the seat tables again: {joined}"


async def test_the_resolver_writes_nothing(app_session: AppSessionFactory) -> None:
    """`upsert=False`, and it matters at every gate rather than only at the
    diagnostic screen.

    `require_permission` guards 110 routes including GETs. A resolver that minted
    a member row would turn every read in the system into a write, and would do
    it on the connection the route is already using -- so a refused caller would
    still have left a row behind.
    """
    tenant = uuid.uuid4()
    subject = f"nie-gesehen-{uuid.uuid4()}"
    async with app_session(tenant) as db:
        with _statements() as issued:
            await authority_for_principal(
                _request(), db, _principal(tenant, subject=subject, role=OPERATOR)
            )
        rows = (
            await db.execute(
                sa.select(sa.func.count())
                .select_from(m.OrgMember)
                .where(m.OrgMember.subject == subject)
            )
        ).scalar_one()

    assert rows == 0
    assert not [s for s in issued if s.lstrip().upper().startswith("INSERT")], (
        f"the gate wrote a row: {issued}"
    )


async def test_a_soft_deleted_member_reads_the_same_way_at_both_doors(
    app_session: AppSessionFactory,
) -> None:
    """The offboarding hole, pinned where it is rather than half-closed.

    `org_member` carries `SoftDeleteMixin` and nothing in `src/oc8` writes
    `deleted_at` in either direction, so this row cannot exist today. When it can
    -- the design's deferred offboarding item -- the fail-open direction is real:
    a person demoted to a four-permission role and then offboarded resolves back
    to whatever their TOKEN says, which on every live tenant is `org_admin`.

    It is not fixed in the resolver alone, and this test is why. Both readers
    filter `deleted_at IS NULL`, and `scope_for_principal(upsert=True)` then MINTS
    a fresh row for the same subject -- so a resolver taught to see the deleted
    row and refuse would answer "nothing" at `require_permission` while
    `require_departmental` walked the same caller in on a brand-new row with no
    assignment. Two doors, two answers, one request.

    So what is asserted is the INVARIANT that has to survive the fix: the two
    readers agree. If somebody closes one of them, this fails and names the other.
    """
    tenant = uuid.uuid4()
    subject = f"anna-{uuid.uuid4()}"
    async with app_session(tenant) as db:
        role_id = await _role(db, tenant, "Freigabe", FREIGABE)
        member = await _member(db, tenant, subject, role_id=role_id)
        principal = _principal(tenant, subject=subject, role=ORG_ADMIN)

        assigned = await authority_for_principal(_request(), db, principal)
        assert assigned.tenant_wide == FREIGABE, "the assignment did not decide to begin with"

        member.deleted_at = dt.datetime.now(tz=dt.UTC)
        await db.flush()

        after = await authority_for_principal(_request(), db, principal)
        seen, _scope = await scope_for_principal(db, principal, upsert=False)

    assert (after.member is None) == (seen is None), (
        "the resolver and the scope disagree about whether a soft-deleted person "
        "exists; one door refuses them and the other mints them a new row"
    )
    assert after.source == "token", (
        "a soft-deleted member no longer falls back to the token. That may well "
        "be right -- but `scope_for_principal(upsert=True)` still mints a fresh "
        "row for this subject, so the two doors now answer differently. Close "
        "both, in the offboarding slice, or neither."
    )
