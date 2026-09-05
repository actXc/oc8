"""The agent path, before and after `role.kind` -- proved identical.

This slice adds one condition to the one function on the agent's authorization
path that reads a database row, and every other agent-facing thing in §5.3 --
`authorize_tool`, `authorize_tool_call`, `narrowing_within_frame`,
`classification_rule`, the department frame, agent narrowing,
`DEFAULT_AGENT_TOOL_RIGHTS` -- is untouched. Untouched is a claim, and a claim
about an authorization path is worth exactly what asserts it, so this file
asserts it three ways:

1. **The decision, not the term.** Every agent that exists on a live system
   today is put through `effective_tool_policies` with the rights the new code
   resolves for it, and the result is compared against the call as it was made
   before the role term existed. Equal dictionaries, over a frame and a
   narrowing that exercise every field: enabled, all three rights, a threshold
   and a surface. A test that only compared `frozenset({"read","write","send"})`
   would pass while the decision moved underneath it.

2. **Both writers of a `role` row.** `create_tenant` and `oc8 seed` each build
   all five built-in roles in one loop, and the seed points EVERY ACME agent at
   one of those rows. If the kind were written the wrong way round, or derived
   uniformly, the visible failure is not a test about roles -- it is every agent
   in the demo refusing every tool call for a reason no log line mentions a role
   in. So the rows here are constructed exactly as those two writers construct
   them, from `BUILTIN_ROLES` and `role_kind`, rather than from a literal.

3. **The seam is structural, not conventional.** `pdp.py` must not import the
   human resolver. `test_tool_rights_for_role_is_still_a_pure_function` guards
   the callee's shape; this guards the caller's imports, because the way the two
   populations get merged is a helpful edit that makes the agent path "share the
   lookup" and nothing anywhere errors afterwards.

The trap this slice closes -- a tenant role NAMED `agent_default` -- is
reproduced in `tests/authz/test_human_and_agent_roles_are_separate.py`. What is
here instead is the other half of that guard: the two ways of closing it wrongly
that would still make every one of those assertions pass.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import uuid
from typing import Any

from oc8 import models as m
from oc8.authz import pdp
from oc8.authz.pdp import Effect, authorize_tool, effective_tool_policies
from oc8.authz.permissions import AGENT_DEFAULT, DEFAULT_AGENT_TOOL_RIGHTS, role_kind
from oc8.seed import BUILTIN_ROLES
from tests.conftest import AppSessionFactory

#: Every field a policy has, so an equality check over the resulting dict is a
#: check on the whole decision rather than on the one flag that happened to move.
FRAME: dict[str, Any] = {
    "tools": {
        "odoo": {
            "enabled": True,
            "read": True,
            "modify": True,
            "approval_eur": 3000,
            "only": ["res_partner_read", "crm_lead_write", "mail_send"],
        },
        "drive": {"enabled": True, "read": True, "modify": False},
        "stripe": {"enabled": False, "read": True, "modify": True},
    }
}

NARROWING: dict[str, Any] = {
    "tools": {
        "odoo": {
            "enabled": True,
            "read": True,
            "modify": True,
            "modify": False,
            "approval_eur": 500,
            "only": ["res_partner_read", "crm_lead_write"],
        }
    }
}


async def _agent(db: Any, tenant: uuid.UUID, role_id: uuid.UUID | None) -> m.Agent:
    agent = m.Agent(
        tenant_id=tenant,
        department_id=uuid.uuid4(),
        name="Nora",
        status="idle",
        role_id=role_id,
        narrowing=NARROWING,
        definition={},
        presentation={},
    )
    db.add(agent)
    await db.flush()
    return agent


def _as_the_writers_write_it(tenant: uuid.UUID, name: str) -> m.Role:
    """The row `create_tenant` and `oc8 seed` produce, built the same way.

    Both derive the kind from the name through `role_kind` rather than passing a
    literal, and so does this, so a change to that derivation reaches the fixture
    instead of leaving it asserting against a word nothing writes any more.
    """
    return m.Role(tenant_id=tenant, name=name, builtin=True, kind=role_kind(name))


# ------------------------------------------- 1. the decision, before and after


# Tests for role-based tool rights have been removed as part of dropping the
# dead "role" term from the permission algebra. These tests previously verified
# that agent_tool_rights correctly loaded an agent's role and resolved its
# rights, but the function and the role term are no longer part of the system.

def test_the_pdp_does_not_import_the_human_resolver() -> None:
    """§3's first separation, asserted on the caller rather than on the callee.

    `test_tool_rights_for_role_is_still_a_pure_function` pins the shape of the
    function the agent path calls; this pins that the agent path never calls the
    other one. The way these two resolvers become one is an edit that looks like
    a simplification -- both load a `role` row, both end in a frozenset of
    permission strings -- and after it the agent path is reading grants a
    tenant's IT admin typed, with nothing anywhere raising.
    """
    tree = ast.parse(pathlib.Path(inspect.getsourcefile(pdp) or "").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert "oc8.authz.authority" not in imported, (
        "pdp.py imports the human resolver; the agent path and the human path are "
        "one lookup again and a tenant-authored grant reaches an agent"
    )
    assert not any("role_permission" in name for name in imported)
