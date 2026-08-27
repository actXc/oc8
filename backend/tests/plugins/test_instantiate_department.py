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
