"""A skill reaches an agent, a department, or everyone.

A skill could only be given to a single agent, so a standard set for a company
meant one assignment per skill per agent, redone by hand whenever somebody was
hired. That is not a limit anybody chose; it is one nobody removed.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.skills.runtime import load_assigned_skills
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

DEFINITION: dict[str, Any] = {
    "schema_version": 1,
    "slug": "erstantwort",
    "version": "1.0.0",
    "instruction": "So schreibst du eine Erstantwort.",
    "requires": {"tools": [], "kbs": []},
    "guardrails": [],
    "presentation": {},
}


async def _skill(db: Any, tenant: uuid.UUID, name: str, slug: str) -> m.SkillVersion:
    skill = m.Skill(tenant_id=tenant, name=name, origin="store", trust_level="first_party")
    db.add(skill)
    await db.flush()
    version = m.SkillVersion(
        tenant_id=tenant, skill_id=skill.id, semver="1.0.0",
        definition={**DEFINITION, "slug": slug}, artifact_hash=b"x" * 32,
    )
    db.add(version)
    await db.flush()
    skill.current_version_id = version.id
    await db.flush()
    return version


async def _dept_with_agent(db: Any, tenant: uuid.UUID, name: str) -> tuple[m.Department, m.Agent]:
    dept = m.Department(tenant_id=tenant, name=name, frame={})
    db.add(dept)
    await db.flush()
    agent = m.Agent(
        tenant_id=tenant, department_id=dept.id, name=f"{name}-Agent", status="idle",
        narrowing={}, definition={}, presentation={},
    )
    db.add(agent)
    await db.flush()
    return dept, agent


async def test_a_department_wide_skill_reaches_every_agent_in_it(
    app_session: AppSessionFactory,
) -> None:
    """The point of the whole change: turn a set on once, and whoever is hired
    afterwards has it without a second act."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept, first = await _dept_with_agent(db, tenant, "Kundenservice")
        version = await _skill(db, tenant, "Ticket-Erstantwort", "erstantwort")
        db.add(
            m.SkillAssignment(
                tenant_id=tenant, department_id=dept.id,
                skill_version_id=version.id, enabled=True, overrides={},
            )
        )
        await db.flush()
        # Hired AFTER the assignment, and never mentioned in it.
        later = m.Agent(
            tenant_id=tenant, department_id=dept.id, name="Neu", status="idle",
            narrowing={}, definition={}, presentation={},
        )
        db.add(later)
        await db.flush()

        for agent in (first, later):
            loaded = await load_assigned_skills(db, agent=agent, tenant_id=tenant)
            assert [s.name for s in loaded] == ["Ticket-Erstantwort"], agent.name


async def test_a_tenant_wide_skill_reaches_another_department(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        _, sales = await _dept_with_agent(db, tenant, "Vertrieb")
        version = await _skill(db, tenant, "Hausstil", "hausstil")
        db.add(
            m.SkillAssignment(
                tenant_id=tenant, skill_version_id=version.id, enabled=True, overrides={},
            )
        )
        await db.flush()
        loaded = await load_assigned_skills(db, agent=sales, tenant_id=tenant)
        assert [s.name for s in loaded] == ["Hausstil"]


async def test_a_department_skill_does_not_leak_to_another_department(
    app_session: AppSessionFactory,
) -> None:
    """Breadth is a grant, not a default. A support procedure must not turn up
    in sales because somebody widened the wrong row."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        support, _ = await _dept_with_agent(db, tenant, "Kundenservice")
        _, sales_agent = await _dept_with_agent(db, tenant, "Vertrieb")
        version = await _skill(db, tenant, "Ticket-Erstantwort", "erstantwort")
        db.add(
            m.SkillAssignment(
                tenant_id=tenant, department_id=support.id,
                skill_version_id=version.id, enabled=True, overrides={},
            )
        )
        await db.flush()
        assert await load_assigned_skills(db, agent=sales_agent, tenant_id=tenant) == []


async def test_the_same_skill_from_two_scopes_is_offered_once(
    app_session: AppSessionFactory,
) -> None:
    """Otherwise the tool list fills with skill_x, skill_x_2 and skill_x_3, all
    identical — and every one of them costs the model attention."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept, agent = await _dept_with_agent(db, tenant, "Kundenservice")
        version = await _skill(db, tenant, "Ticket-Erstantwort", "erstantwort")
        db.add_all(
            [
                m.SkillAssignment(
                    tenant_id=tenant, agent_id=agent.id,
                    skill_version_id=version.id, enabled=True, overrides={},
                ),
                m.SkillAssignment(
                    tenant_id=tenant, department_id=dept.id,
                    skill_version_id=version.id, enabled=True, overrides={},
                ),
                m.SkillAssignment(
                    tenant_id=tenant, skill_version_id=version.id,
                    enabled=True, overrides={},
                ),
            ]
        )
        await db.flush()
        loaded = await load_assigned_skills(db, agent=agent, tenant_id=tenant)
        assert len(loaded) == 1
        assert not loaded[0].tool_name.endswith("_2"), "no suffixed twin"


async def test_an_agent_assignment_still_works_exactly_as_before(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        _, agent = await _dept_with_agent(db, tenant, "Kundenservice")
        version = await _skill(db, tenant, "Ticket-Erstantwort", "erstantwort")
        db.add(
            m.SkillAssignment(
                tenant_id=tenant, agent_id=agent.id,
                skill_version_id=version.id, enabled=True, overrides={},
            )
        )
        await db.flush()
        assert [s.name for s in await load_assigned_skills(db, agent=agent, tenant_id=tenant)] == [
            "Ticket-Erstantwort"
        ]
