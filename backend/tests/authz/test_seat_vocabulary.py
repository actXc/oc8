"""What a seat may carry, and the two words it may be called.

This is the guard against the flaw that killed both source designs for the
department-scoped workspace. In each of them the "Head of Sales" was minted with
the built-in `dept_manager` role -- `_OPERATOR` plus nine `:manage` grants, every
one of them TENANT-WIDE, because `require_permission` structurally cannot carry a
resource. Only his approvals list was scoped; he could still rewrite
Engineering's department frame, retune Engineering's agents and read
Engineering's transcripts.

So the assertion that matters is not "a seat grants approvals". It is that a seat
grants NOTHING ELSE, and that the set is closed by something a future change has
to walk past deliberately.
"""

from __future__ import annotations

import pytest

from oc8.authz.permissions import (
    AGENT,
    ALL_PERMISSIONS,
    APPROVAL_DECIDE,
    APPROVAL_DECIDE_ANY,
    APPROVAL_VIEW_ANY,
    AUDITOR,
    BUILTIN_ROLE_PERMISSIONS,
    CLARIFICATION_ANSWER,
    CLARIFICATION_VIEW,
    DELEGATABLE_PERMISSIONS,
    DEPARTMENT_SCOPABLE,
    DEPT_MANAGER,
    MANAGE,
    OPERATOR,
    ORG_ADMIN,
    SEAT_APPROVER,
    SEAT_PERMISSIONS,
    SEAT_VIEWER,
    perm,
    permissions_for,
    role_has,
    seat_permissions_for,
)

#: The six, written out here rather than derived from SEAT_PERMISSIONS, so that
#: widening the dict cannot widen its own test. Grew from four to six the day
#: `agent:view` and `department:view` graduated into the view level of BOTH seat
#: roles (the department-scoped-agent-authority design's read side): read is not
#: decide-gated, so both `dept_viewer` and `dept_approver` carry it.
_THE_ONLY_SIX = frozenset(
    {
        "approval:view",
        "approval:decide",
        "clarification:view",
        "clarification:answer",
        "agent:view",
        "department:view",
    }
)


def test_a_seat_cannot_carry_a_tenant_wide_permission() -> None:
    """Every permission any seat grants is one of six, and each of the six is
    reachable only through a route that resolves a department before answering.

    A seventh entry here is not a configuration change: it is a claim that some
    other route has been made department-aware, and this is where that claim has
    to be made out loud."""
    granted = frozenset().union(*SEAT_PERMISSIONS.values())
    assert granted == _THE_ONLY_SIX, (
        "a seat carries a right in ONE department; anything else in this set is "
        "tenant-wide the moment a route without a department term reads it"
    )
    for reachable_everywhere in (
        "agent:manage",
        "department:manage",
        "run:start",
        "run:control",
        "knowledge:view",
        "member:manage",
        APPROVAL_VIEW_ANY,
        APPROVAL_DECIDE_ANY,
    ):
        assert reachable_everywhere not in granted, reachable_everywhere


def test_agent_manage_never_enters_the_permission_catalogue() -> None:
    """The seat-native `agent_manage` toggle (`org_member_department.agent_manage`)
    is never a `resource:action` string, and that is what makes it structurally --
    not just conventionally -- unreachable through the tenant-defined-role
    catalogue: `require_agent_write`/`authorize_agent_write` take no permission
    argument, so there is no `perm()` call anywhere in the write path a role
    builder could target in the first place.

    `agent:manage` itself (the STRING, still tenant-wide and still
    `NEVER_DELEGATABLE`) must therefore never show up in any of the three places
    a permission a seat, a role, or a departmental route could reach it."""
    granted = frozenset().union(*SEAT_PERMISSIONS.values())
    assert perm(AGENT, MANAGE) not in granted
    assert perm(AGENT, MANAGE) not in DEPARTMENT_SCOPABLE
    assert perm(AGENT, MANAGE) not in DELEGATABLE_PERMISSIONS


def test_no_seat_names_a_built_in_role() -> None:
    """`dept_manager` in a seat is the original flaw in one word. The seat
    vocabulary and the role vocabulary are disjoint namespaces on purpose, so a
    seat_role can never be read as a role name by a later resolver."""
    assert set(SEAT_PERMISSIONS) == {SEAT_VIEWER, SEAT_APPROVER}
    assert not set(SEAT_PERMISSIONS) & set(BUILTIN_ROLE_PERMISSIONS)


def test_a_viewer_seat_does_not_decide() -> None:
    """The two seat roles differ by exactly the two acting rights. If they did
    not, there would be no reason for the column to exist."""
    viewer = SEAT_PERMISSIONS[SEAT_VIEWER]
    approver = SEAT_PERMISSIONS[SEAT_APPROVER]
    assert APPROVAL_DECIDE not in viewer
    assert CLARIFICATION_ANSWER not in viewer
    assert viewer < approver
    assert approver - viewer == {APPROVAL_DECIDE, CLARIFICATION_ANSWER}


def test_an_unknown_seat_role_carries_nothing() -> None:
    """Fail-closed, the same direction as `permissions_for`: the unknown thing is
    the caller's authority. The CHECK constraint makes an unknown seat_role
    unreachable through the API; this keeps it harmless if a future migration
    ever writes one this code predates."""
    assert seat_permissions_for("dept_manager") == frozenset()
    assert seat_permissions_for("dept_aprover") == frozenset()
    assert seat_permissions_for("") == frozenset()


@pytest.mark.parametrize("seat_role", sorted(SEAT_PERMISSIONS))
def test_no_seat_claims_a_permission_that_does_not_exist(seat_role: str) -> None:
    unknown = SEAT_PERMISSIONS[seat_role] - ALL_PERMISSIONS
    assert not unknown, f"{seat_role} carries undefined permissions: {sorted(unknown)}"


def test_unrestricted_is_a_permission_nobody_but_the_admin_is_born_with() -> None:
    """`approval:decide_any` is what "the whole company's approvals" is made of.
    Granting it to a built-in role would hand every holder of that role every
    department back, which is the change this whole slice exists to undo."""
    deciders = {r for r, p in BUILTIN_ROLE_PERMISSIONS.items() if APPROVAL_DECIDE_ANY in p}
    assert deciders == {ORG_ADMIN}
    viewers = {r for r, p in BUILTIN_ROLE_PERMISSIONS.items() if APPROVAL_VIEW_ANY in p}
    assert viewers == {ORG_ADMIN, AUDITOR}


def test_neither_any_permission_is_swept_in_by_the_view_everything_rule() -> None:
    """`_VIEW_EVERYTHING` sweeps every permission ending in `:view`. Both of
    these deliberately do not, and that is why they are spelled `view_any` rather
    than `any:view` -- a sweep that caught them would silently hand every viewing
    role the whole tenant's approvals."""
    assert not APPROVAL_VIEW_ANY.endswith(":view")
    assert not APPROVAL_DECIDE_ANY.endswith(":view")
    # dept_manager is the widest non-admin role and holds every swept view.
    assert role_has(DEPT_MANAGER, "approval:view")
    assert not role_has(DEPT_MANAGER, APPROVAL_VIEW_ANY)


def test_an_operator_is_admitted_at_the_door_and_narrowed_behind_it() -> None:
    """The accepted behaviour change, pinned. `operator` keeps `approval:decide`,
    so `require_departmental(approval:decide)` still admits him -- but he holds no
    `decide_any`, so the funnel narrows him to the departments he has seats in.
    Nobody loses anything today: only `org_admin` is mintable."""
    assert role_has(OPERATOR, APPROVAL_DECIDE)
    assert not role_has(OPERATOR, APPROVAL_DECIDE_ANY)


def test_answering_a_question_travels_with_deciding_an_approval() -> None:
    """Both are a person unblocking a parked run. A role that could sign off a
    held tool call but not answer the question the agent asked instead would be a
    distinction nobody could explain to the person holding it."""
    for role in (OPERATOR, DEPT_MANAGER, ORG_ADMIN):
        assert role_has(role, APPROVAL_DECIDE) == role_has(role, CLARIFICATION_ANSWER), role
    assert not role_has(AUDITOR, CLARIFICATION_ANSWER)
    # Reading one is a plain view and is swept to everyone who may look at things.
    assert role_has(AUDITOR, CLARIFICATION_VIEW)


def test_enrolling_somebody_in_a_department_is_the_admins_alone() -> None:
    """A Head of Sales who can grant himself a seat in Engineering is the
    department boundary in a different coat -- so `member:manage` is in no seat
    vocabulary AND not in `dept_manager`, which otherwise holds nine `:manage`
    grants."""
    holders = {r for r, p in BUILTIN_ROLE_PERMISSIONS.items() if perm("member", "manage") in p}
    assert holders == {ORG_ADMIN}
    assert not any(perm("member", "manage") in p for p in SEAT_PERMISSIONS.values())
    # Reading the list of people, by contrast, is a plain view.
    assert role_has(DEPT_MANAGER, perm("member", "view"))


def test_the_agent_role_gains_nothing_from_any_of_this() -> None:
    """Six permissions were added to the vocabulary. An agent token is refused by
    the operator API as a whole, and `agent_default` must stay exactly the three
    tool rights -- a widening here would be invisible until it was used."""
    assert permissions_for("agent_default") == {"tool:read", "tool:write", "tool:send"}
