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
from oc8.authz.pdp import Effect, agent_tool_rights, authorize_tool, effective_tool_policies
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
            "write": True,
            "send": True,
            "approval_eur": 3000,
            "only": ["res_partner_read", "crm_lead_write", "mail_send"],
        },
        "drive": {"enabled": True, "read": True, "write": False, "send": False},
        "stripe": {"enabled": False, "read": True, "write": True, "send": True},
    }
}

NARROWING: dict[str, Any] = {
    "tools": {
        "odoo": {
            "enabled": True,
            "read": True,
            "write": True,
            "send": False,
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


async def test_an_agent_without_a_role_decides_exactly_as_it_did(
    app_session: AppSessionFactory,
) -> None:
    """Every agent on the live system is in this state: `role_id` is NULL.

    The row is never loaded, so the new condition cannot be reached at all -- and
    that is asserted against the FULL policy dictionary, not against the rights
    frozenset, because the term feeds a decision and the decision is what an
    agent acts on.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = await _agent(db, tenant, None)
        rights = await agent_tool_rights(db, agent)

    assert rights == DEFAULT_AGENT_TOOL_RIGHTS
    # The right-hand side is the call as it was made before the role term
    # existed: frame and narrowing deciding alone.
    assert effective_tool_policies(FRAME, NARROWING, role_rights=rights) == (
        effective_tool_policies(FRAME, NARROWING)
    )


async def test_a_seeded_agent_decides_exactly_as_it_did(
    app_session: AppSessionFactory,
) -> None:
    """The other shape that exists today: `seed/__init__.py` points every ACME
    agent's `role_id` at the built-in `agent_default` row.

    This is the assertion that fails -- loudly, and in the shape of "the demo
    stopped working" -- if the kind check is written the wrong way round or if a
    writer ever stops deriving the kind from the name.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        role = _as_the_writers_write_it(tenant, AGENT_DEFAULT)
        db.add(role)
        await db.flush()
        agent = await _agent(db, tenant, role.id)
        rights = await agent_tool_rights(db, agent)

    assert rights == DEFAULT_AGENT_TOOL_RIGHTS
    assert effective_tool_policies(FRAME, NARROWING, role_rights=rights) == (
        effective_tool_policies(FRAME, NARROWING)
    )
    # And the same at the door the runtime actually calls, threshold and all.
    assert authorize_tool(FRAME, NARROWING, tool_key="odoo", action="read").effect is Effect.ALLOW
    assert (
        authorize_tool(FRAME, NARROWING, tool_key="odoo", action="send", value_eur=900.0).effect
        is Effect.DENY  # the narrowing withheld `send`; nothing about roles moved it
    )
    assert authorize_tool(FRAME, NARROWING, tool_key="stripe", action="read").effect is Effect.DENY


# --------------------------------------------- 2. both writers of a `role` row


async def test_every_builtin_role_resolves_as_the_writers_kind_says(
    app_session: AppSessionFactory,
) -> None:
    """All five rows, built from `BUILTIN_ROLES` exactly as the two writers build
    them, each with an agent pointed at it.

    One of the five grants the agent vocabulary and four grant nothing, and which
    is which is decided by `role_kind` in one place. A loop that wrote a uniform
    kind passes every test that only ever constructs `agent_default`; this one
    names the whole set, so the uniform case is the case it fails on.
    """
    tenant = uuid.uuid4()
    granted: dict[str, frozenset[str]] = {}
    async with app_session(tenant) as db:
        for name in BUILTIN_ROLES:
            role = _as_the_writers_write_it(tenant, name)
            db.add(role)
            await db.flush()
            granted[name] = await agent_tool_rights(db, await _agent(db, tenant, role.id))

    assert granted[AGENT_DEFAULT] == DEFAULT_AGENT_TOOL_RIGHTS
    assert {n: r for n, r in granted.items() if r} == {AGENT_DEFAULT: DEFAULT_AGENT_TOOL_RIGHTS}, (
        "a role written for a PERSON reached the agent's vocabulary"
    )


# ------------------------------------- 3. the two wrong ways to close the trap


async def test_builtin_is_not_the_discriminator(app_session: AppSessionFactory) -> None:
    """The first wrong fix, and it passes every test that exists elsewhere.

    `builtin` looks like it separates the populations -- the five rows both
    writers produce all carry it, and `POST /roles` will never set it -- so
    `if not role.builtin: return frozenset()` closes the tenant-authored case and
    reads as if it closed the trap. It does not: `builtin` is a plain boolean
    column with no CHECK behind it and nothing that owns it, so a row arriving by
    restore, by psql or from an importer carries whatever it says it carries,
    while `kind` is the column the two resolvers agreed to split the table on.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        impostor = m.Role(tenant_id=tenant, name=AGENT_DEFAULT, kind="human", builtin=True)
        db.add(impostor)
        await db.flush()
        agent = await _agent(db, tenant, impostor.id)

        assert await agent_tool_rights(db, agent) == frozenset()


async def test_a_kind_this_release_has_never_heard_of_grants_nothing() -> None:
    """The second wrong fix: `kind == 'human'` instead of `kind != 'agent'`.

    Today `ck_role_kind` makes a third value unrepresentable, so the two spellings
    are indistinguishable in Postgres and a test that went through the database
    could not tell them apart. But dropping and widening that CHECK is precisely
    what the next `kind` is -- tenant-defined agent roles are deferred item 4, a
    service population is one migration -- and on the day it widens, the deny-list
    spelling hands the new kind every tool right there is while the allow-list
    spelling grants it nothing until somebody decides.

    So the row is handed to the resolver directly, past the constraint that is
    the only reason this is unreachable. `db` is `Any` on this function precisely
    because it asks one thing of its session.
    """

    class _OneRow:
        def __init__(self, row: object) -> None:
            self._row = row

        async def get(self, _model: object, _pk: object) -> object:
            return self._row

    role_id = uuid.uuid4()
    future = m.Role(tenant_id=uuid.uuid4(), id=role_id, name=AGENT_DEFAULT, kind="service")
    agent = m.Agent(
        tenant_id=uuid.uuid4(),
        department_id=uuid.uuid4(),
        name="Nora",
        status="idle",
        role_id=role_id,
        narrowing={},
        definition={},
        presentation={},
    )

    assert await agent_tool_rights(_OneRow(future), agent) == frozenset(), (
        "an unrecognised population was admitted; the check is a deny-list"
    )


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
