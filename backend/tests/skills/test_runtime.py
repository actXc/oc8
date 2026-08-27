from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.skills.runtime import (
    SKILL_TOOL_PREFIX,
    catalog_block,
    instruction_block,
    load_assigned_skills,
    skill_tool_schemas,
)
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


async def _agent_with_skill(
    db: Any, tenant: uuid.UUID, *, definition: dict[str, Any] = DEF, enabled: bool = True
) -> tuple[m.Agent, m.Skill, m.SkillVersion]:
    dept_id = uuid.uuid4()
    agent = m.Agent(
        id=uuid.uuid4(), tenant_id=tenant, department_id=dept_id, name="A",
        role_title="R", mission="M", status="idle", definition={}, presentation={},
    )
    skill = m.Skill(
        id=uuid.uuid4(), tenant_id=tenant, name="Invoice Review",
        description="Validates invoices against POs.", author="oc8 core",
    )
    db.add_all([agent, skill])
    await db.flush()
    version = m.SkillVersion(
        id=uuid.uuid4(), tenant_id=tenant, skill_id=skill.id, semver="1.0.0",
        definition=definition, artifact_hash=b"\x00" * 32,
    )
    db.add(version)
    await db.flush()
    skill.current_version_id = version.id
    db.add(
        m.SkillAssignment(
            id=uuid.uuid4(), tenant_id=tenant, agent_id=agent.id,
            skill_version_id=version.id, enabled=enabled,
        )
    )
    await db.flush()
    return agent, skill, version


async def test_loads_an_enabled_assignment(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, skill, version = await _agent_with_skill(db, tenant)
        loaded = await load_assigned_skills(db, agent=agent, tenant_id=tenant)
        assert len(loaded) == 1
        s = loaded[0]
        assert s.skill_id == skill.id
        assert s.skill_version_id == version.id
        assert s.name == "Invoice Review"
        assert s.tool_name.startswith(SKILL_TOOL_PREFIX)
        assert s.definition.instruction.startswith("Match invoices")


async def test_disabled_assignments_are_skipped(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, _, _ = await _agent_with_skill(db, tenant, enabled=False)
        assert await load_assigned_skills(db, agent=agent, tenant_id=tenant) == []


async def test_unparseable_definition_is_skipped_not_raised(
    app_session: AppSessionFactory,
) -> None:
    # A broken skill must not fail a run that is otherwise fine.
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, _, _ = await _agent_with_skill(db, tenant, definition={"oc8_skill": 1})
        assert await load_assigned_skills(db, agent=agent, tenant_id=tenant) == []


async def test_dangling_skill_version_is_skipped_with_a_warning(
    app_session: AppSessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    # No FK on skill_assignment.skill_version_id -- an assignment can point at
    # a version that has since gone (e.g. its skill was deleted). It must
    # drop out of the result, but with a warning, not silently.
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept_id = uuid.uuid4()
        agent = m.Agent(
            id=uuid.uuid4(), tenant_id=tenant, department_id=dept_id, name="C",
            role_title="R", mission="M", status="idle", definition={}, presentation={},
        )
        db.add(agent)
        await db.flush()
        dangling_version_id = uuid.uuid4()
        db.add(
            m.SkillAssignment(
                id=uuid.uuid4(), tenant_id=tenant, agent_id=agent.id,
                skill_version_id=dangling_version_id, enabled=True,
            )
        )
        await db.flush()

        # Alembic's fileConfig (run once per session by the `settings_env`
        # fixture, via alembic.ini's disable_existing_loggers default of
        # True) disables any logger that already exists at that point --
        # including this one, created at collection time by this module's
        # top-level `from oc8.skills.runtime import ...`. Undo that so
        # caplog can actually observe records from it (same root cause
        # noted in tests/skills/test_schema.py).
        logging.getLogger("oc8.skills.runtime").disabled = False
        with caplog.at_level(logging.WARNING, logger="oc8.skills.runtime"):
            loaded = await load_assigned_skills(db, agent=agent, tenant_id=tenant)

        assert loaded == []
        assert any(
            "not found" in record.getMessage() and str(dangling_version_id) in record.getMessage()
            for record in caplog.records
        )


async def test_agent_without_assignments_gets_an_empty_list(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent = m.Agent(
            id=uuid.uuid4(), tenant_id=tenant, department_id=uuid.uuid4(), name="B",
            role_title="R", mission="M", status="idle", definition={}, presentation={},
        )
        db.add(agent)
        await db.flush()
        assert await load_assigned_skills(db, agent=agent, tenant_id=tenant) == []


async def test_tool_schemas_are_named_and_described(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, _, _ = await _agent_with_skill(db, tenant)
        loaded = await load_assigned_skills(db, agent=agent, tenant_id=tenant)
        tools = skill_tool_schemas(loaded)
        assert len(tools) == 1
        assert tools[0].name == loaded[0].tool_name
        assert "invoice" in tools[0].description.lower()
        assert tools[0].parameters["type"] == "object"


async def test_tool_names_are_unique_when_slugs_collide(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, _, _ = await _agent_with_skill(db, tenant)
        # A second skill with the SAME slug must not produce a duplicate tool name,
        # or the model cannot address them separately.
        skill2 = m.Skill(id=uuid.uuid4(), tenant_id=tenant, name="Invoice Review 2",
                         description="Other.", author="x")
        db.add(skill2)
        await db.flush()
        v2 = m.SkillVersion(id=uuid.uuid4(), tenant_id=tenant, skill_id=skill2.id,
                            semver="2.0.0", definition=DEF, artifact_hash=b"\x01" * 32)
        db.add(v2)
        await db.flush()
        db.add(m.SkillAssignment(id=uuid.uuid4(), tenant_id=tenant, agent_id=agent.id,
                                 skill_version_id=v2.id, enabled=True))
        await db.flush()
        loaded = await load_assigned_skills(db, agent=agent, tenant_id=tenant)
        names = [s.tool_name for s in loaded]
        assert len(names) == len(set(names)) == 2


def test_catalog_block_lists_each_skill() -> None:
    from oc8.skills.runtime import LoadedSkill
    from oc8.skills.schema import parse_definition

    skills = [
        LoadedSkill(
            skill_id=uuid.uuid4(), skill_version_id=uuid.uuid4(), name="Invoice Review",
            description="Validates invoices.", tool_name="skill_invoice_review",
            definition=parse_definition(DEF), creator_id=None,
        )
    ]
    block = catalog_block(skills)
    assert "Invoice Review" in block
    assert "skill_invoice_review" in block
    assert catalog_block([]) == ""


def test_instruction_block_carries_the_instruction_and_prose_guardrails() -> None:
    from oc8.skills.runtime import LoadedSkill
    from oc8.skills.schema import parse_definition

    d = parse_definition({**DEF, "guardrails": ["Never post above 10000 EUR"]})
    s = LoadedSkill(
        skill_id=uuid.uuid4(), skill_version_id=uuid.uuid4(), name="Invoice Review",
        description="d", tool_name="skill_invoice_review", definition=d, creator_id=None,
    )
    block = instruction_block(s)
    assert "Match invoices" in block
    assert "Never post above 10000 EUR" in block
