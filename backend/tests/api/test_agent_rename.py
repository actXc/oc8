"""`PATCH /agents/{id}/name` lets an admin rename an agent from its detail
page (the pencil-icon UI beside the agent's name)."""

from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _token(tenant: uuid.UUID, role: str = "org_admin") -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role=role)


async def _make_agent(app_session: AppSessionFactory, tenant: uuid.UUID) -> uuid.UUID:
    async with app_session(tenant) as db:
        department = m.Department(tenant_id=tenant, name=f"D-{uuid.uuid4().hex}", frame={})
        db.add(department)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=department.id, name="Lennart")
        db.add(agent)
        await db.flush()
        return agent.id


async def test_admin_can_rename_an_agent_and_it_persists(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    agent_id = await _make_agent(app_session, tenant)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            updated = await client.patch(
                f"/api/v1/agents/{agent_id}/name",
                json={"name": "Nora"},
                headers=headers,
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["name"] == "Nora"

            fetched = await client.get(f"/api/v1/agents/{agent_id}", headers=headers)
            assert fetched.status_code == 200, fetched.text
            assert fetched.json()["name"] == "Nora"


async def test_non_admin_cannot_rename_an_agent(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    agent_id = await _make_agent(app_session, tenant)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.patch(
                f"/api/v1/agents/{agent_id}/name",
                json={"name": "Should not land"},
                headers={"Authorization": f"Bearer {_token(tenant, 'member')}"},
            )
            assert response.status_code == 403, response.text


async def test_renaming_to_an_empty_string_is_rejected(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    agent_id = await _make_agent(app_session, tenant)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            response = await client.patch(
                f"/api/v1/agents/{agent_id}/name",
                json={"name": ""},
                headers=headers,
            )
            assert response.status_code == 422, response.text
