"""GET /knowledge/bases/{kb_id}/chunks/{chunk_id}/similar (Knowledge Base
Content View plan, Task 2).

Follows the fixture conventions of test_kb_chunks.py (Task 1): DB setup
happens in an `app_session` block before the app is created, and the
app/client are opened in their own `LifespanManager` block afterward.

Embedding vectors are padded to `EMBED_DIM` (768, per
`oc8.models.knowledge`) since pgvector enforces the declared column
dimension -- a 3-float vector would fail to insert.
"""

from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from oc8.models.knowledge import EMBED_DIM
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID, role: str = "org_admin") -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role=role)
    return {"Authorization": f"Bearer {token}"}


def _client(app: object) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _vec(*head: float) -> list[float]:
    """Pad a short, human-readable direction vector out to EMBED_DIM."""
    return [*head, *([0.0] * (EMBED_DIM - len(head)))]


async def _kb(db: object, tenant: uuid.UUID) -> m.KnowledgeBase:
    kb = m.KnowledgeBase(
        tenant_id=tenant,
        name="Similarity KB",
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
    content: str,
    embedding: list[float] | None,
    source_uri: str = "doc-a",
) -> m.KbChunk:
    c = m.KbChunk(
        tenant_id=tenant,
        kb_id=kb_id,
        content=content,
        source_uri=source_uri,
        classification="internal",
        chunk_metadata={},
        embedding=embedding,
    )
    db.add(c)
    await db.flush()
    return c


async def test_similar_chunks_ranks_by_cosine_distance_and_excludes_self(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        kb = await _kb(db, tenant)
        kb_id = kb.id
        query = await _chunk(db, tenant, kb_id, content="query", embedding=_vec(1.0, 0.0, 0.0))
        close = await _chunk(db, tenant, kb_id, content="close", embedding=_vec(0.9, 0.1, 0.0))
        far = await _chunk(db, tenant, kb_id, content="far", embedding=_vec(0.0, 0.0, 1.0))
        query_id, close_id, far_id = query.id, close.id, far.id

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            resp = await c.get(
                f"/api/v1/knowledge/bases/{kb_id}/chunks/{query_id}/similar",
                headers=_headers(tenant),
            )
            assert resp.status_code == 200, resp.text
            items = resp.json()
            ids = [i["id"] for i in items]
            assert str(query_id) not in ids
            assert ids.index(str(close_id)) < ids.index(str(far_id))
            assert all("similarity" in i for i in items)


async def test_similar_chunks_404s_for_unknown_chunk(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        kb = await _kb(db, tenant)
        kb_id = kb.id

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            resp = await c.get(
                f"/api/v1/knowledge/bases/{kb_id}/chunks/{uuid.uuid4()}/similar",
                headers=_headers(tenant),
            )
            assert resp.status_code == 404


async def test_similar_chunks_excludes_chunks_without_embeddings(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        kb = await _kb(db, tenant)
        kb_id = kb.id
        query = await _chunk(db, tenant, kb_id, content="query", embedding=_vec(1.0, 0.0, 0.0))
        query_id = query.id
        await _chunk(db, tenant, kb_id, content="unembedded", embedding=None)

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            resp = await c.get(
                f"/api/v1/knowledge/bases/{kb_id}/chunks/{query_id}/similar",
                headers=_headers(tenant),
            )
            assert resp.status_code == 200, resp.text
            assert resp.json() == []


async def test_similar_chunks_requires_manage_permission(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        kb = await _kb(db, tenant)
        kb_id = kb.id
        query = await _chunk(db, tenant, kb_id, content="q", embedding=_vec(1.0, 0.0, 0.0))
        query_id = query.id

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            for role in ("auditor", "operator", "dept_manager"):
                resp = await c.get(
                    f"/api/v1/knowledge/bases/{kb_id}/chunks/{query_id}/similar",
                    headers=_headers(tenant, role),
                )
                assert resp.status_code == 403, role
