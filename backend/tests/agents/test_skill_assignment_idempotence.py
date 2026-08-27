"""Assigning a skill an agent already has is not an error.

Live, 2026-07-29: the assign button in the UI returned 500. The unique index
had reported the duplicate faithfully and the exception went straight out as an
Internal Server Error — which tells an operator that oc8 broke, when in fact
nothing needed doing.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

DEFINITION: dict[str, Any] = {
    "schema_version": 1,
    "slug": "s",
    "version": "1.0.0",
    "instruction": "Tu etwas.",
    "requires": {"tools": [], "kbs": []},
    "guardrails": [],
    "presentation": {},
}


async def _agent_and_version(db: Any, tenant: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    dept = m.Department(tenant_id=tenant, name="Kundenservice", frame={})
    db.add(dept)
    await db.flush()
    agent = m.Agent(
        tenant_id=tenant, department_id=dept.id, name="Sina", status="idle",
        narrowing={}, definition={}, presentation={},
    )
    skill = m.Skill(tenant_id=tenant, name="S", origin="local", trust_level="first_party")
    db.add_all([agent, skill])
    await db.flush()
    version = m.SkillVersion(
        tenant_id=tenant, skill_id=skill.id, semver="1.0.0",
        definition=DEFINITION, artifact_hash=b"x" * 32,
    )
    db.add(version)
    await db.flush()
    skill.current_version_id = version.id
    await db.flush()
    await db.commit()
    return agent.id, version.id


async def test_assigning_twice_answers_the_same_way_both_times(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, version_id = await _agent_and_version(db, tenant)

    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role="org_admin")
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            first = await c.post(
                f"/api/v1/agents/{agent_id}/skills",
                headers={"Authorization": f"Bearer {token}"},
                json={"skillVersionId": str(version_id)},
            )
            second = await c.post(
                f"/api/v1/agents/{agent_id}/skills",
                headers={"Authorization": f"Bearer {token}"},
                json={"skillVersionId": str(version_id)},
            )

    assert first.status_code < 400, first.text
    assert second.status_code < 400, second.text
    assert first.json()["status"] == "assigned"
    assert second.json()["status"] == "already_assigned", "and it says so, rather than lying"

    async with app_session(tenant) as db:
        rows = (
            (
                await db.execute(
                    select(m.SkillAssignment).where(m.SkillAssignment.agent_id == agent_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
