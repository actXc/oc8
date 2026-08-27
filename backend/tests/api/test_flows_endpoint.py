from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.collab.handoff import create_handoff_type
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _token(tenant: uuid.UUID) -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")


def _spec(from_dept: uuid.UUID, to_dept: uuid.UUID) -> dict[str, object]:
    return {
        "id": "flow-http",
        "version": "1.0.0",
        "trigger": {"event": "sales.deal.won", "from_department_id": str(from_dept)},
        "stages": [
            {
                "id": "kickoff",
                "handoff": {"type": "flowhttp.kickoff", "to_department_id": str(to_dept)},
            },
            {
                "id": "build",
                "after": "kickoff.completed",
                "handoff": {"type": "flowhttp.build", "to_department_id": str(to_dept)},
            },
        ],
    }


async def test_publish_start_and_advance_on_handoff_complete(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    from_dept, to_dept = uuid.uuid4(), uuid.uuid4()
    async with app_session(tenant) as db:
        for name in ("flowhttp.kickoff", "flowhttp.build"):
            await create_handoff_type(
                db, tenant_id=tenant, name=name, payload_schema={"type": "object"}
            )

    app = create_app()
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            pub = await client.post(
                "/api/v1/flows", json={"spec": _spec(from_dept, to_dept)}, headers=headers
            )
            assert pub.status_code == 201
            version_id = pub.json()["id"]

            started = await client.post(
                f"/api/v1/flow-versions/{version_id}/start",
                json={"context": {"customer": "Acme"}},
                headers=headers,
            )
            assert started.status_code == 201
            run_id = started.json()["id"]
            assert started.json()["currentStages"] == ["kickoff"]

            # find the kickoff handoff the flow created
            async with app_session(tenant) as db:
                kickoff = (
                    await db.execute(
                        select(m.Handoff).where(m.Handoff.flow_run_id == uuid.UUID(run_id))
                    )
                ).scalar_one()
                kickoff_id = str(kickoff.id)

            # accept + complete it over HTTP -> flow advances to the build stage
            await client.post(f"/api/v1/handoffs/{kickoff_id}/accept", json={}, headers=headers)
            await client.post(f"/api/v1/handoffs/{kickoff_id}/complete", headers=headers)

            run = await client.get(f"/api/v1/flow-runs/{run_id}", headers=headers)
            assert run.status_code == 200
            assert run.json()["currentStages"] == ["build"]
            assert run.json()["status"] == "running"
