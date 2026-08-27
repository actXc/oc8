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


async def test_create_base_upload_text_and_pdf(app_session: AppSessionFactory) -> None:
    import base64

    tenant = uuid.UUID(str(ACME_TENANT_ID))
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                "/api/v1/knowledge/bases",
                json={"name": "Handbook", "description": "test kb"},
                headers=headers,
            )
            assert r.status_code == 201, r.text
            kb_id = r.json()["id"]

            r = await client.post(
                f"/api/v1/knowledge/bases/{kb_id}/documents",
                json={
                    "filename": "a.txt",
                    "contentType": "text/plain",
                    "content": "hello from a text file",
                },
                headers=headers,
            )
            assert r.status_code == 200, r.text
            assert r.json()["docs"] == 1

            encoded = base64.b64encode(b"not a real pdf, will fail parse").decode()
            r = await client.post(
                f"/api/v1/knowledge/bases/{kb_id}/documents",
                json={"filename": "b.pdf", "contentType": "application/pdf", "content": encoded},
                headers=headers,
            )
            assert r.status_code == 422, r.text


async def test_upload_document_unsupported_content_type_is_422(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    async with app_session(tenant) as db:
        kb = m.KnowledgeBase(tenant_id=tenant, name="K", embedding_model="nomic-embed-text")
        db.add(kb)
        await db.flush()
        kb_id = kb.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                f"/api/v1/knowledge/bases/{kb_id}/documents",
                json={
                    "filename": "x.bin",
                    "contentType": "application/octet-stream",
                    "content": "x",
                },
                headers=headers,
            )
            assert r.status_code == 422, r.text


async def test_base_source_ids_reflect_real_ingested_sources(
    app_session: AppSessionFactory,
) -> None:
    """`KnowledgeBase.source_ids` (the JSONB column) is never written by any
    ingestion path -- `sourceIds` on the DTO must instead be derived from
    live KbChunk rows (`linked_source_ids` in knowledge/chunks.py), or every
    base would report an empty Sources list regardless of what has actually
    been synced into it."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                "/api/v1/knowledge/bases",
                json={"name": "Sourced KB", "description": "test"},
                headers=headers,
            )
            assert r.status_code == 201, r.text
            kb_id = r.json()["id"]
            assert r.json()["sourceIds"] == []

            r = await client.get(f"/api/v1/knowledge/bases/{kb_id}", headers=headers)
            assert r.status_code == 200, r.text
            assert r.json()["sourceIds"] == []

            r = await client.get("/api/v1/knowledge/bases", headers=headers)
            assert r.status_code == 200, r.text
            listed = next(b for b in r.json()["items"] if b["id"] == kb_id)
            assert listed["sourceIds"] == []

            r = await client.post(
                f"/api/v1/knowledge/bases/{kb_id}/documents",
                json={
                    "filename": "handbook.txt",
                    "contentType": "text/plain",
                    "content": "onboarding steps",
                },
                headers=headers,
            )
            assert r.status_code == 200, r.text
            upload_response_source_ids = r.json()["sourceIds"]
            assert len(upload_response_source_ids) == 1

            r = await client.get(f"/api/v1/knowledge/bases/{kb_id}", headers=headers)
            assert r.status_code == 200, r.text
            assert r.json()["sourceIds"] == upload_response_source_ids

            r = await client.get("/api/v1/knowledge/bases", headers=headers)
            assert r.status_code == 200, r.text
            listed = next(b for b in r.json()["items"] if b["id"] == kb_id)
            assert listed["sourceIds"] == upload_response_source_ids


async def test_unlink_source_removes_it_from_source_ids_and_is_idempotent(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                "/api/v1/knowledge/bases",
                json={"name": "Unlink KB", "description": "test"},
                headers=headers,
            )
            assert r.status_code == 201, r.text
            kb_id = r.json()["id"]

            r = await client.post(
                f"/api/v1/knowledge/bases/{kb_id}/documents",
                json={
                    "filename": "notes.txt",
                    "contentType": "text/plain",
                    "content": "some notes",
                },
                headers=headers,
            )
            assert r.status_code == 200, r.text
            (source_id,) = r.json()["sourceIds"]

            r = await client.delete(
                f"/api/v1/knowledge/bases/{kb_id}/sources/{source_id}", headers=headers
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["kbId"] == kb_id
            assert body["dataSourceId"] == source_id
            assert body["documents"] == 1
            assert body["chunks"] >= 1
            assert body["auditSeq"] is not None

            r = await client.get(f"/api/v1/knowledge/bases/{kb_id}", headers=headers)
            assert r.status_code == 200, r.text
            assert r.json()["sourceIds"] == []

            # Idempotent: a retry/double-click on an already-unlinked pair is a
            # 200 no-op receipt, not a 404 -- it did not fail, it has nothing
            # left to do.
            r = await client.delete(
                f"/api/v1/knowledge/bases/{kb_id}/sources/{source_id}", headers=headers
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["documents"] == 0
            assert body["chunks"] == 0
            assert body["auditSeq"] is None


async def test_unlink_source_unknown_kb_or_source_is_404(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    async with app_session(tenant) as db:
        kb = m.KnowledgeBase(tenant_id=tenant, name="K", embedding_model="nomic-embed-text")
        ds = m.DataSource(tenant_id=tenant, connector_type="upload", name="S", connected=True)
        db.add_all([kb, ds])
        await db.flush()
        kb_id, source_id = kb.id, ds.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.delete(
                f"/api/v1/knowledge/bases/{uuid.uuid4()}/sources/{source_id}", headers=headers
            )
            assert r.status_code == 404, r.text

            r = await client.delete(
                f"/api/v1/knowledge/bases/{kb_id}/sources/{uuid.uuid4()}", headers=headers
            )
            assert r.status_code == 404, r.text


async def test_upload_document_unknown_kb_is_404(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                f"/api/v1/knowledge/bases/{uuid.uuid4()}/documents",
                json={"filename": "x.txt", "contentType": "text/plain", "content": "x"},
                headers=headers,
            )
            assert r.status_code == 404, r.text


async def test_create_grant_and_idempotent_recreate(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    async with app_session(tenant) as db:
        kb = m.KnowledgeBase(tenant_id=tenant, name="K2", embedding_model="nomic-embed-text")
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="A")
        db.add_all([kb, agent])
        await db.flush()
        kb_id, agent_id = kb.id, agent.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            body = {"kbId": str(kb_id), "granteeType": "agent", "granteeId": str(agent_id)}
            r1 = await client.post("/api/v1/knowledge/grants", json=body, headers=headers)
            assert r1.status_code == 201, r1.text
            grant_id_1 = r1.json()["id"]

            r2 = await client.post("/api/v1/knowledge/grants", json=body, headers=headers)
            assert r2.status_code == 201, r2.text
            assert r2.json()["id"] == grant_id_1  # idempotent, same row

    async with app_session(tenant) as db:
        from sqlalchemy import select

        rows = (
            (await db.execute(select(m.KnowledgeGrant).where(m.KnowledgeGrant.kb_id == kb_id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1


async def test_create_grant_unknown_kb_or_grantee_is_404(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    headers = {"Authorization": f"Bearer {_token(tenant)}"}
    async with app_session(tenant) as db:
        kb = m.KnowledgeBase(tenant_id=tenant, name="K3", embedding_model="nomic-embed-text")
        db.add(kb)
        await db.flush()
        kb_id = kb.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                "/api/v1/knowledge/grants",
                json={
                    "kbId": str(uuid.uuid4()),
                    "granteeType": "agent",
                    "granteeId": str(uuid.uuid4()),
                },
                headers=headers,
            )
            assert r.status_code == 404, r.text

            r = await client.post(
                "/api/v1/knowledge/grants",
                json={"kbId": str(kb_id), "granteeType": "agent", "granteeId": str(uuid.uuid4())},
                headers=headers,
            )
            assert r.status_code == 404, r.text
