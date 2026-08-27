"""The transcript preamble both runtimes must put in front of the model.

This exists because the isolated runtime used to seed only the system prompt,
leaving an agent under isolation blind to its memory, its knowledge base, its
department roster and its skills -- which in turn made delegate_task unusable
(no valid agent_id to name) and memory_write pointless (nothing ever read it
back).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.preamble import build_run_preamble
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

DEF: dict[str, Any] = {
    "oc8_skill": 1,
    "id": "sk-invoice-check",
    "version": "1.0.0",
    "instruction": "Match invoices against purchase orders.",
    "requires": {"tools": [{"tool": "odoo", "rights": ["read", "write"]}], "kbs": []},
    "guardrails": [],
}


async def _lead_with_mate_and_skill(
    db: Any, tenant: uuid.UUID
) -> tuple[m.Agent, m.Agent]:
    dept = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
    db.add(dept)
    await db.flush()
    lead = m.Agent(
        tenant_id=tenant, department_id=dept.id, name="Nora", role_title="Lead",
        mission="Sell things.", status="idle", definition={}, presentation={},
        is_team_lead=True,
    )
    mate = m.Agent(
        tenant_id=tenant, department_id=dept.id, name="Rico", role_title="Rep",
        status="idle", definition={}, presentation={},
    )
    skill = m.Skill(
        tenant_id=tenant, name="Invoice Review",
        description="Validates invoices against POs.", author="oc8 core",
    )
    db.add_all([lead, mate, skill])
    await db.flush()
    version = m.SkillVersion(
        tenant_id=tenant, skill_id=skill.id, semver="1.0.0",
        definition=DEF, artifact_hash=b"\x00" * 32,
    )
    db.add(version)
    await db.flush()
    skill.current_version_id = version.id
    db.add(
        m.SkillAssignment(
            tenant_id=tenant, agent_id=lead.id, skill_version_id=version.id, enabled=True
        )
    )
    await db.flush()
    return lead, mate


async def test_a_team_lead_preamble_carries_roster_and_skill_catalog(
    app_session: AppSessionFactory,
) -> None:
    """A team lead can only name a real agent_id if it is told its colleagues,
    and can only invoke a skill it is told about."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        lead, mate = await _lead_with_mate_and_skill(db, tenant)

        pre = await build_run_preamble(
            db, agent=lead, tenant_id=tenant, task_text="Erstelle ein Angebot",
            frame={}, model_locality="eu",
        )

    joined = "\n".join(msg.content for msg in pre.messages)
    assert str(mate.id) in joined, "the roster must name the mate's real id"
    assert "Rico" in joined
    assert "Invoice Review" in joined, "the skills catalog must be seeded"
    assert pre.skill_tool_names, "skill tool names drive the frame bypass in _authorize"

    # The task the agent was given is the LAST message, and the only user turn --
    # everything before it is context the core supplies.
    assert pre.messages[-1].role == "user"
    assert pre.messages[-1].content == "Erstelle ein Angebot"
    assert [msg.role for msg in pre.messages[:-1]] == ["system"] * (len(pre.messages) - 1)
    assert pre.messages[0].content.startswith("You are Nora")


async def test_a_plain_agent_gets_no_roster_and_no_delegation_context(
    app_session: AppSessionFactory,
) -> None:
    """Only a team lead delegates, so only a team lead is told the roster."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Ops", frame={})
        db.add(dept)
        await db.flush()
        solo = m.Agent(
            tenant_id=tenant, department_id=dept.id, name="Solo", status="idle",
            definition={}, presentation={},
        )
        mate = m.Agent(
            tenant_id=tenant, department_id=dept.id, name="Nachbar", status="idle",
            definition={}, presentation={},
        )
        db.add_all([solo, mate])
        await db.flush()

        pre = await build_run_preamble(
            db, agent=solo, tenant_id=tenant, task_text="mach was",
            frame={}, model_locality="eu",
        )

    joined = "\n".join(msg.content for msg in pre.messages)
    assert str(mate.id) not in joined
    assert pre.assigned_skills == []
    assert pre.skill_tool_names == frozenset()


async def test_the_provenance_rule_precedes_anything_a_stranger_wrote(
    app_session: AppSessionFactory,
) -> None:
    """Said once, before any external content can arrive. A ticket body is
    written by someone outside the company and lands in the same context as
    oc8's own mission; without this it arrives in the same voice."""
    from oc8.agent.provenance import RULE

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Kundenservice", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant, department_id=dept.id, name="Sina", status="idle",
            definition={}, presentation={},
        )
        db.add(agent)
        await db.flush()
        pre = await build_run_preamble(
            db, agent=agent, tenant_id=tenant, task_text="Bearbeite ein Ticket",
            frame={}, model_locality="cloud",
        )
    systems = [msg.content for msg in pre.messages if msg.role == "system"]
    assert RULE in systems
