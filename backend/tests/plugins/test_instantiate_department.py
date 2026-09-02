from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.capas.service import PluginError, instantiate_department
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _version(
    agents: list[dict[str, object]] | None = None, ptype: str = "department_template"
) -> m.CapaVersion:
    spec: dict[str, object] = {
        "frame": {"tools": {}, "kbs": [], "memory": {}},
        "agents": agents
        if agents is not None
        else [
            {
                "name": "Head of Sales",
                "role_title": "Lead",
                "mission": "sell",
                "is_team_lead": True,
                "persona": "# Lead",
                "skills": ["crm"],
            },
            {"name": "Rep A", "reports_to": "Head of Sales", "persona": "# A"},
            {"name": "Rep B", "reports_to": "Head of Sales", "persona": "# B"},
        ],
    }
    return m.CapaVersion(
        tenant_id=uuid.uuid4(),
        capa_id=uuid.uuid4(),
        semver="1.0.0",
        manifest={"name": "sales", "version": "1.0.0", "type": ptype, "department_template": spec},
        artifact_hash=b"x",
        permissions=[],
        capabilities=[],
        entry_points={},
    )


async def test_instantiate_creates_department_and_stopped_agents(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        version = _version()
        dept = await instantiate_department(s, tenant_id=tenant, version=version, name="Sales")
        assert dept.name == "Sales" and dept.frame == {"tools": {}, "kbs": [], "memory": {}}
        agents = (
            (await s.execute(select(m.Agent).where(m.Agent.department_id == dept.id)))
            .scalars()
            .all()
        )
        assert len(agents) == 3
        assert all(a.status == "stopped" for a in agents)
        lead = next(a for a in agents if a.is_team_lead)
        assert lead.name == "Head of Sales"
        assert dept.team_lead_agent_id == lead.id
        rep = next(a for a in agents if a.name == "Rep A")
        assert rep.definition["reports_to"] == "Head of Sales"
        assert rep.definition["persona"] == "# A"
        # each agent got an agent-tier memory store
        stores = (
            (await s.execute(select(m.MemoryStore).where(m.MemoryStore.tier == "agent")))
            .scalars()
            .all()
        )
        assert {st.owner_id for st in stores} == {a.id for a in agents}


async def test_wrong_type_rejected(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        with pytest.raises(PluginError):
            await instantiate_department(
                s, tenant_id=tenant, version=_version(ptype="agent_template"), name="X"
            )


async def test_duplicate_name_and_bad_reports_to_rejected(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        with pytest.raises(PluginError):
            await instantiate_department(
                s,
                tenant_id=tenant,
                name="X",
                version=_version(agents=[{"name": "A"}, {"name": "A"}]),
            )
        with pytest.raises(PluginError):
            await instantiate_department(
                s,
                tenant_id=tenant,
                name="X",
                version=_version(agents=[{"name": "A", "reports_to": "Ghost"}]),
            )


async def test_instantiate_creates_trigger_when_agent_has_one(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        version = _version(
            agents=[
                {
                    "name": "Nora",
                    "is_team_lead": True,
                    "trigger": {
                        "kind": "cron",
                        "cron_expression": "0 8 * * 1-5",
                        "task_text": "Tagesreport erstellen",
                    },
                },
            ]
        )
        dept = await instantiate_department(s, tenant_id=tenant, version=version, name="Sales")
        agent = (
            await s.execute(select(m.Agent).where(m.Agent.department_id == dept.id))
        ).scalar_one()
        trigger = (
            await s.execute(select(m.Trigger).where(m.Trigger.agent_id == agent.id))
        ).scalar_one()
        assert trigger.kind == "cron"
        assert trigger.cron_expression == "0 8 * * 1-5"
        assert trigger.task_text == "Tagesreport erstellen"
        assert trigger.enabled is True


async def test_instantiate_creates_skill_assignment_for_existing_local_skill(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        skill = m.Skill(tenant_id=tenant, name="crm", origin="local")
        s.add(skill)
        await s.flush()
        skill_version = m.SkillVersion(
            tenant_id=tenant,
            skill_id=skill.id,
            semver="1.0.0",
            definition={"schema_version": 1},
            artifact_hash=b"x",
        )
        s.add(skill_version)
        await s.flush()
        skill.current_version_id = skill_version.id
        await s.flush()

        version = _version(
            agents=[{"name": "Head of Sales", "is_team_lead": True, "skills": ["crm"]}]
        )
        dept = await instantiate_department(s, tenant_id=tenant, version=version, name="Sales")
        agent = (
            await s.execute(select(m.Agent).where(m.Agent.department_id == dept.id))
        ).scalar_one()
        assignment = (
            await s.execute(select(m.SkillAssignment).where(m.SkillAssignment.agent_id == agent.id))
        ).scalar_one()
        assert assignment.skill_version_id == skill_version.id
        assert assignment.enabled is True


async def test_instantiate_skips_missing_skill_silently(app_session: AppSessionFactory) -> None:
    """A named skill that doesn't (yet) exist in the target tenant -- e.g. its
    sibling `skill` capa in the ZIP hasn't been installed yet -- must not
    fail the whole department instantiate. Matches materialise.py's own
    "malformed data is logged and skipped, not raised" convention."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        version = _version(
            agents=[{"name": "Head of Sales", "is_team_lead": True, "skills": ["nonexistent"]}]
        )
        dept = await instantiate_department(s, tenant_id=tenant, version=version, name="Sales")
        agent = (
            await s.execute(select(m.Agent).where(m.Agent.department_id == dept.id))
        ).scalar_one()
        assignments = (
            await s.execute(select(m.SkillAssignment).where(m.SkillAssignment.agent_id == agent.id))
        ).scalars().all()
        assert assignments == []
