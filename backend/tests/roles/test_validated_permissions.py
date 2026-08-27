"""`validated_permissions`'s exit condition for `agent:view`/`department:view`,
and the one string that must never follow them out.

Both permissions' own `NOT_YET_DELEGATABLE` comments named this in advance --
"graduates with the departmental read term" -- and this design is that term
landing, for exactly these two. `test_a_delegatable_permission_changes_no_
configuration` (`tests/api/test_roles_endpoint.py`) already asserts the set
arithmetic once the dict entries move; this file asserts the WRITER agrees, at
the level `validated_permissions` itself operates: an admin who tries to put one
of these two in a tenant-defined role gets a 201, not a 422 that used to be
correct and would now be a lie.

`agent:manage` staying refused is the other half of the same claim, asserted in
the same file so a future edit that widens the dict has to look at both
outcomes at once: reads graduate, the one WRITE permission this slice adds does
not, and it does not because it is never a string in `ALL_PERMISSIONS` in the
first place -- decision 2, made structural, not merely refused here by policy.
"""

from __future__ import annotations

import pytest

from oc8.authz.permissions import AGENT, DEPARTMENT, MANAGE, VIEW, perm
from oc8.roles.service import RoleRefused, validated_permissions


def test_agent_view_and_department_view_are_now_delegatable() -> None:
    """The setup that used to 422 for both of these now succeeds -- and, laid
    out with a third genuinely-delegatable permission alongside them, does not
    silently pass because the WHOLE list was accepted."""
    requested = [perm(AGENT, VIEW), perm(DEPARTMENT, VIEW), perm(AGENT, VIEW)]
    result = validated_permissions(requested)
    assert result == frozenset({perm(AGENT, VIEW), perm(DEPARTMENT, VIEW)})


def test_agent_manage_is_still_refused_unconditionally() -> None:
    """The one write permission this slice's mechanism concerns itself with
    NEVER enters this set -- it is not merely refused by `NEVER_DELEGATABLE`'s
    entry, it structurally cannot become reachable through a tenant-defined role
    at all (see `authz/test_seat_vocabulary.py::
    test_agent_manage_never_enters_the_permission_catalogue`); this pins the
    other half, that the writer itself still names it and refuses it."""
    with pytest.raises(RoleRefused) as excinfo:
        validated_permissions([perm(AGENT, VIEW), perm(AGENT, MANAGE)])
    assert excinfo.value.status_code == 422
    assert perm(AGENT, MANAGE) in excinfo.value.detail


def test_agent_manage_is_refused_even_alone() -> None:
    with pytest.raises(RoleRefused) as excinfo:
        validated_permissions([perm(AGENT, MANAGE)])
    assert excinfo.value.status_code == 422
    assert perm(AGENT, MANAGE) in excinfo.value.detail
