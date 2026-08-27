from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.collab.handoff import create_handoff_type
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _token(tenant: uuid.UUID) -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")


async def test_binding_and_emit_over_http(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        sales = m.Department(
            tenant_id=tenant, name="sales-http-emit", frame={"emits": ["sales.deal.won.http"]}
        )
        eng = m.Department(
            tenant_id=tenant,
            name="eng-http-emit",
            frame={
                "intakes": [{"handoff_type": "http.kickoff", "route": "team_lead", "gate": "auto"}]
            },
        )
        db.add_all([sales, eng])
        await db.flush()
        ht = await create_handoff_type(
            db,
            tenant_id=tenant,
            name="http.kickoff",
            payload_schema={"type": "object", "required": ["customer"]},
        )
        sales_id, eng_id, ht_id = str(sales.id), str(eng.id), str(ht.id)

    app = create_app()
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            b = await client.post(
                "/api/v1/contract-bindings",
                json={
                    "eventType": "sales.deal.won.http",
                    "handoffTypeId": ht_id,
                    "sourceDepartmentId": sales_id,
                    "targetDepartmentId": eng_id,
                    "payloadMap": {"customer": "$.customer"},
                },
                headers=headers,
            )
            assert b.status_code == 201

            emit = await client.post(
                f"/api/v1/departments/{sales_id}/emit",
                json={"eventType": "sales.deal.won.http", "payload": {"customer": "Acme"}},
                headers=headers,
            )
            assert emit.status_code == 200
            assert len(emit.json()["handoffs"]) == 1

            denied = await client.post(
                f"/api/v1/departments/{eng_id}/emit",  # eng does not emit this
                json={"eventType": "sales.deal.won.http", "payload": {}},
                headers=headers,
            )
            assert denied.status_code == 403


async def test_department_contracts_get_put_roundtrip(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="contracts-crud", frame={})
        db.add(dept)
        await db.flush()
        dept_id = str(dept.id)

    app = create_app()
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            # empty to start
            r0 = await client.get(f"/api/v1/departments/{dept_id}/contracts", headers=headers)
            assert r0.status_code == 200
            assert r0.json() == {"emits": [], "intakes": []}

            # declare emits + an intake with a gate + fields
            r1 = await client.put(
                f"/api/v1/departments/{dept_id}/contracts",
                json={
                    "emits": ["sales.deal.won", "sales.deal.won"],  # dupes collapse
                    "intakes": [
                        {
                            "id": "in-1",
                            "type": "project.kickoff",
                            "route": "Team Lead",
                            "gate": "approval",
                            "fields": [{"name": "customer", "type": "string", "required": True}],
                        }
                    ],
                },
                headers=headers,
            )
            assert r1.status_code == 200
            body = r1.json()
            assert body["emits"] == ["sales.deal.won"]
            assert body["intakes"][0]["type"] == "project.kickoff"
            assert body["intakes"][0]["gate"] == "approval"
            assert body["intakes"][0]["fields"][0]["name"] == "customer"

            # read back persists (stored as handoff_type in the frame)
            r2 = await client.get(f"/api/v1/departments/{dept_id}/contracts", headers=headers)
            assert r2.json()["intakes"][0]["type"] == "project.kickoff"

    # persisted intake is readable by the PEP helper (handoff_type key)
    from oc8.collab.contracts import department_intake

    async with app_session(tenant) as db:
        dept = await db.get(m.Department, uuid.UUID(dept_id))
        assert dept is not None
        assert department_intake(dept.frame, "project.kickoff") is not None
