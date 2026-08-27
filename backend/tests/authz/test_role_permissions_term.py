"""§5.3's first term: role_permissions(agent.role).

    effective = role_permissions(agent.role) ∩ department_frame ∩ agent_narrowing

The frame and the narrowing were enforced from the start. The role term was
written in the spec and absent from the code -- the decision simply began at the
frame -- so `agent.role_id` was a column nothing read.

The risk in adding a term to a live authorization path is not that it fails to
restrict. It is that it changes a decision somebody is relying on. Every agent on
this system has `role_id = NULL`, which resolves to `agent_default` and grants
all three rights, so the first job of this file is to prove that nothing moved.

Since tenant-defined roles, the row must also name the right POPULATION. `role`
holds humans and agents in one table and `agent_tool_rights` used to resolve a
row back through the code dictionary by its NAME alone, so a role a tenant
called `agent_default` reached the agent's whole vocabulary. The rows below
therefore pass `kind=` explicitly: a role that grants an agent anything is
`kind="agent"`, and that word -- not the name -- is what the PDP now asks about.
`tests/authz/test_human_and_agent_roles_are_separate.py` is where the trap
itself is reproduced; here it only means the fixtures have to be honest about
which population they are building.
"""

from __future__ import annotations

import uuid
from typing import Any

from oc8 import models as m
from oc8.authz.pdp import agent_tool_rights, effective_tool_policies
from oc8.authz.permissions import tool_rights_for_role
from tests.conftest import AppSessionFactory

FRAME: dict[str, Any] = {
    "tools": {
        "odoo": {"enabled": True, "read": True, "write": True, "send": True, "approval_eur": 3000}
    }
}


def test_the_default_changes_no_decision() -> None:
    """What every agent alive gets. If this ever differs, the term stopped being
    inert and every existing agent's behaviour moved with it."""
    without = effective_tool_policies(FRAME, {})
    with_default = effective_tool_policies(FRAME, {}, role_rights=tool_rights_for_role(None))
    assert without == with_default
    assert with_default["odoo"].read
    assert with_default["odoo"].write
    assert with_default["odoo"].send


def test_a_read_only_role_takes_write_and_send_away() -> None:
    """The point of the term: one role, reusable across agents, doing what a
    per-agent narrowing can only do one agent at a time."""
    eff = effective_tool_policies(FRAME, {}, role_rights=frozenset({"read"}))

    assert eff["odoo"].read
    assert not eff["odoo"].write
    assert not eff["odoo"].send
    # Still offered -- the tool is reachable, the action is not. A tool that
    # vanished would tell the model it does not exist, which is a different and
    # less honest answer than refusing the write.
    assert eff["odoo"].enabled


def test_a_role_can_never_widen_the_frame() -> None:
    """Same rule as the narrowing, and the reason all three terms are intersected
    rather than merged: naming a right the frame withholds must not grant it."""
    frame_read_only = {
        "tools": {"odoo": {"enabled": True, "read": True, "write": False, "send": False}}
    }

    eff = effective_tool_policies(
        frame_read_only, {}, role_rights=frozenset({"read", "write", "send"})
    )

    assert eff["odoo"].read
    assert not eff["odoo"].write
    assert not eff["odoo"].send


def test_a_role_and_a_narrowing_both_apply() -> None:
    """Neither term is a substitute for the other, so the strictest of the three
    wins on every right independently."""
    narrowing = {"tools": {"odoo": {"enabled": True, "read": True, "write": True, "send": False}}}

    eff = effective_tool_policies(FRAME, narrowing, role_rights=frozenset({"read"}))

    assert eff["odoo"].read
    assert not eff["odoo"].write  # removed by the role
    assert not eff["odoo"].send  # removed by the narrowing


def test_an_empty_role_leaves_a_reachable_tool_with_no_action() -> None:
    eff = effective_tool_policies(FRAME, {}, role_rights=frozenset())

    assert eff["odoo"].enabled
    assert not any((eff["odoo"].read, eff["odoo"].write, eff["odoo"].send))


async def test_an_agent_without_a_role_gets_all_three_rights(
    app_session: AppSessionFactory,
) -> None:
    """The live state: three agents, none carrying a role_id."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = m.Agent(
            tenant_id=tenant,
            department_id=uuid.uuid4(),
            name="Sina",
            status="idle",
            narrowing={},
            definition={},
            presentation={},
        )
        db.add(agent)
        await db.flush()

        assert await agent_tool_rights(db, agent) == frozenset({"read", "write", "send"})


async def test_an_assigned_role_is_read_from_the_role_row(
    app_session: AppSessionFactory,
) -> None:
    """`agent.role_id` is a foreign key to `role`, and the name on that row is
    what resolves to a permission bundle -- once the row has proved it belongs to
    the agent population at all. This is the row both writers build: `builtin`,
    named `agent_default`, `kind` derived from the name by `role_kind`."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        role = m.Role(tenant_id=tenant, name="agent_default", builtin=True, kind="agent")
        db.add(role)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant,
            department_id=uuid.uuid4(),
            name="Sina",
            status="idle",
            role_id=role.id,
            narrowing={},
            definition={},
            presentation={},
        )
        db.add(agent)
        await db.flush()

        assert await agent_tool_rights(db, agent) == frozenset({"read", "write", "send"})


async def test_a_human_role_on_an_agent_grants_no_tool_rights(
    app_session: AppSessionFactory,
) -> None:
    """`operator` and `auditor` describe what a PERSON may do in the control
    plane. Reading either as permission to act on a customer's systems would be
    exactly the quiet widening this layer exists to prevent.

    Two independent things now refuse it and the row is written honestly so that
    the stronger one answers first: `kind='human'` puts it in the other
    population, and even if the kind check were removed the name `auditor`
    carries no `tool:*` permission in the code dictionary."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        role = m.Role(tenant_id=tenant, name="auditor", builtin=True, kind="human")
        db.add(role)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant,
            department_id=uuid.uuid4(),
            name="Sina",
            status="idle",
            role_id=role.id,
            narrowing={},
            definition={},
            presentation={},
        )
        db.add(agent)
        await db.flush()

        assert await agent_tool_rights(db, agent) == frozenset()


async def test_a_dangling_role_reference_grants_nothing(
    app_session: AppSessionFactory,
) -> None:
    """The uncomfortable direction, and the right one: a role_id pointing at a
    row that is gone means the deployment does not know what this agent may do.
    An agent that stops acting is visible and reversible; one that acts on a
    guess is neither."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = m.Agent(
            tenant_id=tenant,
            department_id=uuid.uuid4(),
            name="Sina",
            status="idle",
            role_id=uuid.uuid4(),  # never existed
            narrowing={},
            definition={},
            presentation={},
        )
        db.add(agent)
        await db.flush()

        assert await agent_tool_rights(db, agent) == frozenset()
