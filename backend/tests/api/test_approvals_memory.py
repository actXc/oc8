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


def _token(tenant: uuid.UUID) -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")


async def test_approving_memory_write_marks_record_approved(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="A")
        db.add(agent)
        await db.flush()
        # Use agent_id as store owner and "agent" tier to ensure uniqueness
        store = m.MemoryStore(
            tenant_id=tenant, tier="agent", owner_id=agent.id
        )
        db.add(store)
        await db.flush()
        record = m.MemoryRecord(
            tenant_id=tenant,
            store_id=store.id,
            content="pending fact",
            written_by=agent.id,
            status="pending",
        )
        db.add(record)
        await db.flush()
        ar = m.ApprovalRequest(
            tenant_id=tenant,
            agent_id=agent.id,
            action_type="memory_write",
            status="pending",
            payload={
                "memory_record_id": str(record.id),
                "tier": "agent",
                "content": "pending fact",
            },
        )
        db.add(ar)
        await db.flush()
        ar_id = ar.id
        record_id = record.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                f"/api/v1/approvals/{ar_id}/decision",
                json={"decision": "approve"},
                headers={"Authorization": f"Bearer {_token(tenant)}"},
            )
            assert r.status_code == 200, r.text

    async with app_session(tenant) as db:
        refreshed = await db.get(m.MemoryRecord, record_id)
        assert refreshed is not None and refreshed.status == "approved"
        # Clean up the approval request to avoid interference with other tests
        ar_to_delete = await db.get(m.ApprovalRequest, ar_id)
        if ar_to_delete is not None:
            await db.delete(ar_to_delete)


async def test_rejecting_memory_write_marks_record_rejected(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="B")
        db.add(agent)
        await db.flush()
        # Use agent_id as store owner to ensure uniqueness
        store = m.MemoryStore(
            tenant_id=tenant, tier="company", owner_id=agent.id
        )
        db.add(store)
        await db.flush()
        record = m.MemoryRecord(
            tenant_id=tenant,
            store_id=store.id,
            content="pending fact 2",
            written_by=agent.id,
            status="pending",
        )
        db.add(record)
        await db.flush()
        ar = m.ApprovalRequest(
            tenant_id=tenant,
            agent_id=agent.id,
            action_type="memory_write",
            status="pending",
            payload={
                "memory_record_id": str(record.id),
                "tier": "company",
                "content": "pending fact 2",
            },
        )
        db.add(ar)
        await db.flush()
        ar_id = ar.id
        record_id = record.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                f"/api/v1/approvals/{ar_id}/decision",
                json={"decision": "reject"},
                headers={"Authorization": f"Bearer {_token(tenant)}"},
            )
            assert r.status_code == 200, r.text

    async with app_session(tenant) as db:
        refreshed = await db.get(m.MemoryRecord, record_id)
        assert refreshed is not None and refreshed.status == "rejected"
        # Clean up the approval request to avoid interference with other tests
        ar_to_delete = await db.get(m.ApprovalRequest, ar_id)
        if ar_to_delete is not None:
            await db.delete(ar_to_delete)
