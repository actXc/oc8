from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.capas.manifest import parse_manifest
from oc8.capas.service import install_plugin, instantiate_department
from oc8.seed.department_templates import SALES_DEPARTMENT_TEMPLATE
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def test_sales_template_manifest_is_valid() -> None:
    mf = parse_manifest(SALES_DEPARTMENT_TEMPLATE)
    assert mf.type == "department_template"
    assert mf.department_template is not None
    leads = [a for a in mf.department_template.agents if a.is_team_lead]
    assert len(leads) == 1  # a Head of Sales
    assert len(mf.department_template.agents) == 3  # lead + 2 reps


async def test_sales_template_installs_and_instantiates(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        version = await install_plugin(
            s, tenant_id=tenant, manifest_data=SALES_DEPARTMENT_TEMPLATE
        )
        dept = await instantiate_department(s, tenant_id=tenant, version=version, name="Sales")
        agents = (await s.execute(
            select(m.Agent).where(m.Agent.department_id == dept.id)
        )).scalars().all()
        assert len(agents) == 3
        assert dept.team_lead_agent_id is not None
