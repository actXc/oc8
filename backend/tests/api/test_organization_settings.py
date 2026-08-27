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


async def test_organization_settings_are_readable_and_admin_editable(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        if await db.get(m.Organization, tenant) is None:
            db.add(m.Organization(id=tenant, slug=f"pilot-{uuid.uuid4().hex}", name="Pilot"))
            await db.flush()
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            before = await client.get("/api/v1/settings/organization", headers=headers)
            assert before.status_code == 200, before.text
            original = before.json()

            changed = await client.put(
                "/api/v1/settings/organization",
                json={"name": "Pilot Workspace", "region": "eu-central"},
                headers=headers,
            )
            assert changed.status_code == 200, changed.text
            assert changed.json() == {
                **original,
                "name": "Pilot Workspace",
                "region": "eu-central",
            }


async def test_non_admin_cannot_edit_organization_settings() -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.put(
                "/api/v1/settings/organization",
                json={"name": "Nope", "region": "eu"},
                headers={"Authorization": f"Bearer {_token(tenant, 'member')}"},
            )
            assert response.status_code == 403, response.text
