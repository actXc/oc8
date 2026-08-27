"""GET /knowledge/bases/{kb_id}/chunks (Knowledge Base Content View plan, Task 1).

Follows the fixture conventions of test_knowledgebase_patch_and_list_query.py:
DB setup happens in an `app_session` block before the app is created, and the
app/client are opened in their own `LifespanManager` block afterward.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID, role: str = "org_admin") -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role=role)
    return {"Authorization": f"Bearer {token}"}


def _client(app: object) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _kb(db: object, tenant: uuid.UUID) -> m.KnowledgeBase:
    kb = m.KnowledgeBase(
        tenant_id=tenant,
        name="Content View KB",
        embedding_model="test/embed",
        status="current",
        classification="internal",
    )
    db.add(kb)
    await db.flush()
    return kb


async def _chunk(
    db: object,
    tenant: uuid.UUID,
    kb_id: uuid.UUID,
    *,
    content: str = "hello world",
    source_uri: str = "doc-a",
    classification: str = "internal",
    deleted: bool = False,
) -> m.KbChunk:
    c = m.KbChunk(
        tenant_id=tenant,
        kb_id=kb_id,
        content=content,
        source_uri=source_uri,
        classification=classification,
        chunk_metadata={},
    )
    if deleted:
        c.deleted_at = dt.datetime.now(tz=dt.UTC)
    db.add(c)
    await db.flush()
    return c


async def test_list_chunks_returns_paged_envelope(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        kb = await _kb(db, tenant)
        kb_id = kb.id
        await _chunk(db, tenant, kb_id, content="apples and oranges")
        await _chunk(db, tenant, kb_id, content="bananas and pears")

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            resp = await c.get(f"/api/v1/knowledge/bases/{kb_id}/chunks", headers=_headers(tenant))
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["totalCount"] == 2
            assert len(body["items"]) == 2
            assert "content" in body["items"][0]
            assert "chunkMetadata" in body["items"][0]


async def test_list_chunks_search_filters_by_content(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        kb = await _kb(db, tenant)
        kb_id = kb.id
        await _chunk(db, tenant, kb_id, content="the quick brown fox")
        await _chunk(db, tenant, kb_id, content="a slow green turtle")

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            resp = await c.get(
                f"/api/v1/knowledge/bases/{kb_id}/chunks?search=fox", headers=_headers(tenant)
            )
            assert resp.status_code == 200, resp.text
            items = resp.json()["items"]
            assert len(items) == 1
            assert "fox" in items[0]["content"]


async def test_list_chunks_source_uri_filters_to_one_document(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        kb = await _kb(db, tenant)
        kb_id = kb.id
        await _chunk(db, tenant, kb_id, content="from doc a", source_uri="doc-a")
        await _chunk(db, tenant, kb_id, content="from doc b", source_uri="doc-b")

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            resp = await c.get(
                f"/api/v1/knowledge/bases/{kb_id}/chunks?sourceUri=doc-a",
                headers=_headers(tenant),
            )
            assert resp.status_code == 200, resp.text
            items = resp.json()["items"]
            assert len(items) == 1
            assert items[0]["sourceUri"] == "doc-a"


async def test_list_chunks_excludes_deleted_chunks(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        kb = await _kb(db, tenant)
        kb_id = kb.id
        await _chunk(db, tenant, kb_id, content="still here")
        await _chunk(db, tenant, kb_id, content="tombstoned", deleted=True)

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            resp = await c.get(f"/api/v1/knowledge/bases/{kb_id}/chunks", headers=_headers(tenant))
            assert resp.status_code == 200, resp.text
            items = resp.json()["items"]
            assert len(items) == 1
            assert items[0]["content"] == "still here"


async def test_list_chunks_requires_manage_permission(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        kb = await _kb(db, tenant)
        kb_id = kb.id

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            # auditor, operator, and dept_manager all hold knowledge:view but
            # not knowledge:manage -- same convention as
            # test_knowledgebase_patch_and_list_query.py's
            # test_patch_base_requires_knowledge_manage.
            for role in ("auditor", "operator", "dept_manager"):
                resp = await c.get(
                    f"/api/v1/knowledge/bases/{kb_id}/chunks",
                    headers=_headers(tenant, role),
                )
                assert resp.status_code == 403, role


async def test_list_chunks_paginates_with_total_count(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        kb = await _kb(db, tenant)
        kb_id = kb.id
        for i in range(5):
            await _chunk(db, tenant, kb_id, content=f"chunk {i}")

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            resp = await c.get(
                f"/api/v1/knowledge/bases/{kb_id}/chunks?limit=2&offset=1",
                headers=_headers(tenant),
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["totalCount"] == 5
            assert len(body["items"]) == 2
