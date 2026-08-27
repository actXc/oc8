from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.knowledge.worker import get_ingestion_queue
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID) -> dict[str, str]:
    tok = get_identity_provider().mint(tenant_id=tenant, subject="op", role="org_admin")
    return {"Authorization": f"Bearer {tok}"}


async def test_sync_returns_queued_without_blocking(
    app_session: AppSessionFactory, redis_url: str
) -> None:
    # Requested, not merely available: "queued without blocking" IS an enqueue to
    # redis. Without the fixture the test needs an ambient one and fails with a
    # connection error that looks like a defect in the endpoint.
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        kb = m.KnowledgeBase(
            tenant_id=tenant, name="kb", description="", embedding_model="nomic-embed-text"
        )
        db.add(kb)
        ds = m.DataSource(tenant_id=tenant, connector_type="website", name="s",
                          config={"url": "https://example.com"})
        db.add(ds)
        await db.flush()
        kb_id, ds_id = kb.id, ds.id
        await db.commit()

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                f"/api/v1/knowledge/sources/{ds_id}/sync",
                json={"kbId": str(kb_id)}, headers=_headers(tenant),
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "queued"   # NOT terminal
            job_id = body["id"]

            # the job is readable and queued
            g = await c.get(f"/api/v1/knowledge/jobs/{job_id}", headers=_headers(tenant))
            assert g.status_code == 200, g.text
            assert g.json()["status"] == "queued"


async def test_job_status_404_cross_tenant(app_session: AppSessionFactory) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    async with app_session(tenant_a) as db:
        kb = m.KnowledgeBase(
            tenant_id=tenant_a, name="kb", description="", embedding_model="nomic-embed-text"
        )
        db.add(kb)
        ds = m.DataSource(tenant_id=tenant_a, connector_type="website", name="s", config={})
        db.add(ds)
        await db.flush()
        job = m.IngestionJob(tenant_id=tenant_a, data_source_id=ds.id, kb_id=kb.id,
                             status="queued")
        db.add(job)
        await db.flush()
        job_id = job.id
        await db.commit()

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get(f"/api/v1/knowledge/jobs/{job_id}", headers=_headers(tenant_b))
            assert r.status_code == 404


@pytest.fixture(autouse=True)
async def _drain_stream() -> AsyncIterator[None]:
    # Keep the shared ingestion stream from accumulating across tests.
    yield
    try:
        q = get_ingestion_queue()
        await q._client.delete(q._key)
    except Exception:
        pass
