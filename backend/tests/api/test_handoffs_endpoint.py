from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8.auth import get_identity_provider
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app

pytestmark = pytest.mark.asyncio


def _token(tenant: uuid.UUID) -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")


async def test_handoff_flow_over_http() -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    app = create_app()
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    schema = {"type": "object", "required": ["customer"]}

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            ht = await client.post(
                "/api/v1/handoff-types",
                json={"name": "project.kickoff.http", "payloadSchema": schema},
                headers=headers,
            )
            assert ht.status_code == 201
            type_id = ht.json()["id"]

            ok = await client.post(
                "/api/v1/handoffs",
                json={
                    "handoffTypeId": type_id,
                    "sourceDepartmentId": str(uuid.uuid4()),
                    "targetDepartmentId": str(uuid.uuid4()),
                    "payload": {"customer": "Acme"},
                },
                headers=headers,
            )
            assert ok.status_code == 201
            hid = ok.json()["id"]
            assert ok.json()["status"] == "pending"

            bad = await client.post(
                "/api/v1/handoffs",
                json={
                    "handoffTypeId": type_id,
                    "sourceDepartmentId": str(uuid.uuid4()),
                    "targetDepartmentId": str(uuid.uuid4()),
                    "payload": {"nope": 1},
                },
                headers=headers,
            )
            assert bad.status_code == 400

            acc = await client.post(f"/api/v1/handoffs/{hid}/accept", json={}, headers=headers)
            assert acc.status_code == 200 and acc.json()["status"] == "accepted"
            comp = await client.post(f"/api/v1/handoffs/{hid}/complete", headers=headers)
            assert comp.status_code == 200 and comp.json()["status"] == "completed"

            lst = await client.get("/api/v1/handoffs", headers=headers)
            assert lst.status_code == 200 and any(h["id"] == hid for h in lst.json())
