"""The five routes that let a customer take something back out.

There is no DELETE anywhere on the knowledge API today, so the only removal path
in the product is raw SQL against `kb_chunk`. These routes are also the whole
answer for a GDPR-shaped request -- nothing schedules a sync, so the sweep may
never run for a given source -- which is why the listing route ships with them:
for an upload the URI is `upload://{server-generated-uuid}/{filename}`, and
without a way to enumerate documents the delete route would be addressable by
nobody.

What the tests below mostly pin is the identity: (tenant, data_source, kb,
source_uri). Two of them fail any design that keys deletion on the URI alone,
and one fails any design that leaves the KB out.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.knowledge.tombstone import BASE_DELETED, tombstone_document
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID, role: str = "org_admin") -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role=role)
    return {"Authorization": f"Bearer {token}"}


async def _mark_absent(db: Any, *, kb_id: uuid.UUID, uri: str, when: dt.datetime) -> None:
    """Fact A, as a sync's observation writes it.

    `tombstone_document(reason="source_absent")` covers only chunks an attested
    listing actually MARKED -- a row with `missing_since IS NULL` carries its own
    proof that no observation ever missed it. So a test standing in for the sweep
    has to write the mark first, exactly as `observe_listing` does; without this
    the fixture builds a state the product cannot reach.
    """
    await db.execute(
        update(m.KbChunk)
        .where(
            m.KbChunk.kb_id == kb_id,
            m.KbChunk.source_uri == uri,
            m.KbChunk.deleted_at.is_(None),
        )
        .values(missing_since=when)
    )
    await db.flush()


async def _org(db: Any, tenant: uuid.UUID) -> None:
    if await db.get(m.Organization, tenant) is None:
        db.add(m.Organization(id=tenant, slug=f"t-{tenant.hex[:8]}", name="T"))
        await db.flush()


async def _kb(db: Any, tenant: uuid.UUID, name: str = "KB") -> m.KnowledgeBase:
    kb = m.KnowledgeBase(tenant_id=tenant, name=name, embedding_model="nomic-embed-text")
    db.add(kb)
    await db.flush()
    return kb


async def _source(db: Any, tenant: uuid.UUID, name: str = "src") -> m.DataSource:
    ds = m.DataSource(
        tenant_id=tenant, connector_type="website", name=name, config={}, connected=True
    )
    db.add(ds)
    await db.flush()
    return ds


async def _document(
    db: Any,
    tenant: uuid.UUID,
    *,
    kb_id: uuid.UUID,
    ds_id: uuid.UUID | None,
    uri: str,
    content: str = "body",
) -> None:
    db.add(
        m.KbChunk(
            tenant_id=tenant,
            kb_id=kb_id,
            data_source_id=ds_id,
            content=content,
            embedding=None,
            source_uri=uri,
            chunk_metadata={"chunk_index": 0},
        )
    )
    await db.flush()


async def _live(db: Any, kb_id: uuid.UUID) -> list[m.KbChunk]:
    rows = await db.execute(
        select(m.KbChunk).where(m.KbChunk.kb_id == kb_id, m.KbChunk.deleted_at.is_(None))
    )
    return list(rows.scalars().all())


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


# ------------------------------------------------------------------ governance


async def test_delete_routes_require_knowledge_manage(app_session: AppSessionFactory) -> None:
    """`knowledge:manage` is org_admin-only today, and that is a product choice
    for a destructive operation rather than something inherited silently: a
    dept_manager cannot erase a document, and clearing a held source needs an
    org admin."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _org(db, tenant)
        kb = await _kb(db, tenant)
        ds = await _source(db, tenant)
        kb_id, ds_id = kb.id, ds.id
        doomed = await _kb(db, tenant, name="doomed")
        doomed_id = doomed.id
        # Its own source: the source delete below would otherwise tombstone this
        # base's only document on its way past, and a base with nothing left in
        # it is a different test.
        other = await _source(db, tenant, name="other")
        await _document(db, tenant, kb_id=doomed_id, ds_id=other.id, uri="test://doomed")
        await _document(db, tenant, kb_id=kb_id, ds_id=ds_id, uri="test://deleted")
        await _document(db, tenant, kb_id=kb_id, ds_id=ds_id, uri="test://restored")
        await _document(db, tenant, kb_id=kb_id, ds_id=ds_id, uri="test://source-owned")
        await _mark_absent(db, kb_id=kb_id, uri="test://restored", when=dt.datetime.now(tz=dt.UTC))
        await tombstone_document(
            db,
            tenant_id=tenant,
            kb_id=kb_id,
            data_source_id=ds_id,
            source_uri="test://restored",
            reason="source_absent",
            reduce_now=False,
            now=dt.datetime.now(tz=dt.UTC),
        )

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            for role in ("auditor", "operator", "dept_manager"):
                h = _headers(tenant, role)
                assert (
                    await c.request(
                        "DELETE",
                        f"/api/v1/knowledge/bases/{kb_id}/documents",
                        params={"sourceUri": "test://deleted"},
                        headers=h,
                    )
                ).status_code == 403, role
                assert (
                    await c.post(
                        f"/api/v1/knowledge/bases/{kb_id}/documents/restore",
                        params={"sourceUri": "test://restored"},
                        headers=h,
                    )
                ).status_code == 403, role
                assert (
                    await c.delete(f"/api/v1/knowledge/sources/{ds_id}", headers=h)
                ).status_code == 403, role
                assert (
                    await c.delete(f"/api/v1/knowledge/bases/{doomed_id}", headers=h)
                ).status_code == 403, role

            admin = _headers(tenant)
            assert (
                await c.request(
                    "DELETE",
                    f"/api/v1/knowledge/bases/{kb_id}/documents",
                    params={"sourceUri": "test://deleted"},
                    headers=admin,
                )
            ).status_code == 200
            assert (
                await c.post(
                    f"/api/v1/knowledge/bases/{kb_id}/documents/restore",
                    params={"sourceUri": "test://restored"},
                    headers=admin,
                )
            ).status_code == 200
            assert (
                await c.delete(f"/api/v1/knowledge/sources/{ds_id}", headers=admin)
            ).status_code == 200
            assert (
                await c.delete(f"/api/v1/knowledge/bases/{doomed_id}", headers=admin)
            ).status_code == 200


# ------------------------------------------------------------------ addressability


async def test_list_documents_makes_the_delete_route_addressable(
    app_session: AppSessionFactory,
) -> None:
    """`source_uri` appears in zero DTOs, zero responses and zero frontend files
    today. Without this route the DELETE ships addressable by nobody: for an
    upload the URI contains a uuid the server invented."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _org(db, tenant)
        kb = await _kb(db, tenant)
        kb_id = kb.id

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            h = _headers(tenant)
            for name in ("erste.txt", "zweite.txt"):
                up = await c.post(
                    f"/api/v1/knowledge/bases/{kb_id}/documents",
                    json={"filename": name, "contentType": "text/plain", "content": f"in {name}"},
                    headers=h,
                )
                assert up.status_code == 200, up.text

            listed = await c.get(f"/api/v1/knowledge/bases/{kb_id}/documents", headers=h)
            assert listed.status_code == 200, listed.text
            docs = listed.json()
            uris = [d["sourceUri"] for d in docs]
            assert len(uris) == 2
            assert all(u.startswith("upload://") for u in uris)
            assert all(d["chunks"] >= 1 for d in docs)

            removed = await c.request(
                "DELETE",
                f"/api/v1/knowledge/bases/{kb_id}/documents",
                params={"sourceUri": uris[0]},
                headers=h,
            )
            assert removed.status_code == 200, removed.text
            assert removed.json()["sourceUri"] == uris[0]

            after = (await c.get(f"/api/v1/knowledge/bases/{kb_id}/documents", headers=h)).json()
            assert [d["sourceUri"] for d in after] == [uris[1]]


async def test_delete_document_without_a_source_id_reaches_legacy_chunks(
    app_session: AppSessionFactory,
) -> None:
    """`dataSourceId` is optional and NARROWING. Omitting it is what "erase this
    document, wherever it landed" means, and it is the only way an operator
    reaches pre-0045 rows whose provenance the migration honestly refused to
    guess. The response reports the blast radius rather than hiding it."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _org(db, tenant)
        kb = await _kb(db, tenant)
        ds = await _source(db, tenant)
        kb_id = kb.id
        await _document(db, tenant, kb_id=kb_id, ds_id=None, uri="https://x/report")
        await _document(db, tenant, kb_id=kb_id, ds_id=ds.id, uri="https://x/report")

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            r = await c.request(
                "DELETE",
                f"/api/v1/knowledge/bases/{kb_id}/documents",
                params={"sourceUri": "https://x/report"},
                headers=_headers(tenant),
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chunks"] == 2
    assert body["sourcesTouched"] == 2, "one attributed copy and one legacy copy"
    async with app_session(tenant) as db:
        assert await _live(db, kb_id) == []


# ------------------------------------------------------------------ identity


async def test_delete_source_leaves_the_other_sources_documents(
    app_session: AppSessionFactory,
) -> None:
    """Two sources legitimately produce the same `source_uri` into one base --
    a website and a Drive copy of the same page, say. This test fails any
    design that keys deletion on `source_uri` alone."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _org(db, tenant)
        kb = await _kb(db, tenant)
        first = await _source(db, tenant, name="first")
        second = await _source(db, tenant, name="second")
        kb_id, first_id, second_id = kb.id, first.id, second.id
        await _document(
            db, tenant, kb_id=kb_id, ds_id=first_id, uri="https://x/shared", content="from first"
        )
        await _document(
            db, tenant, kb_id=kb_id, ds_id=second_id, uri="https://x/shared", content="from second"
        )

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            r = await c.delete(f"/api/v1/knowledge/sources/{first_id}", headers=_headers(tenant))
    assert r.status_code == 200, r.text
    assert r.json()["sourceId"] == str(first_id)

    async with app_session(tenant) as db:
        live = await _live(db, kb_id)
        assert [c.content for c in live] == ["from second"]
        assert live[0].data_source_id == second_id
        retired = await db.get(m.DataSource, first_id)
        assert retired is not None and retired.deleted_at is not None, "retired, not vaporised"
        assert retired.connected is False and retired.schedule_cron is None


async def test_one_source_feeding_two_bases_deletes_only_the_named_base(
    app_session: AppSessionFactory,
) -> None:
    """A DataSource has no KB of its own -- the target base is a per-request
    parameter of the sync route -- so one source legitimately feeds several
    bases. An identity that omitted `kb_id` would delete out of all of them."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _org(db, tenant)
        named = await _kb(db, tenant, name="named")
        other = await _kb(db, tenant, name="other")
        ds = await _source(db, tenant)
        named_id, other_id, ds_id = named.id, other.id, ds.id
        for kb_id in (named_id, other_id):
            await _document(db, tenant, kb_id=kb_id, ds_id=ds_id, uri="https://x/policy")

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            r = await c.request(
                "DELETE",
                f"/api/v1/knowledge/bases/{named_id}/documents",
                params={"sourceUri": "https://x/policy", "dataSourceId": str(ds_id)},
                headers=_headers(tenant),
            )
    assert r.status_code == 200, r.text

    async with app_session(tenant) as db:
        assert await _live(db, named_id) == []
        assert len(await _live(db, other_id)) == 1


async def test_delete_base_removes_its_grants_and_hides_it(
    app_session: AppSessionFactory,
) -> None:
    """A grant is an access rule, not a record, and `_grants_for` reads every
    grant unconditionally -- so a dangling grant on a deleted base is an authz
    hazard rather than history worth keeping."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _org(db, tenant)
        kb = await _kb(db, tenant)
        ds = await _source(db, tenant)
        kb_id = kb.id
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="A")
        db.add(agent)
        await db.flush()
        db.add(
            m.KnowledgeGrant(
                tenant_id=tenant, kb_id=kb_id, grantee_type="agent", grantee_id=agent.id
            )
        )
        await _document(
            db, tenant, kb_id=kb_id, ds_id=ds.id, uri="https://x/secret", content="secret text"
        )

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            h = _headers(tenant)
            r = await c.delete(f"/api/v1/knowledge/bases/{kb_id}", headers=h)
            assert r.status_code == 200, r.text
            listed = await c.get("/api/v1/knowledge/bases", headers=h)
    assert [b["id"] for b in listed.json()["items"]] == []

    async with app_session(tenant) as db:
        grants = (
            (await db.execute(select(m.KnowledgeGrant).where(m.KnowledgeGrant.kb_id == kb_id)))
            .scalars()
            .all()
        )
        assert grants == []
        chunks = (
            (await db.execute(select(m.KbChunk).where(m.KbChunk.kb_id == kb_id))).scalars().all()
        )
        assert all(c.content == "" and c.reduced_at is not None for c in chunks)
        events = (
            (
                await db.execute(
                    select(m.AuditEvent).where(
                        m.AuditEvent.tenant_id == tenant, m.AuditEvent.action == BASE_DELETED
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].responsible_type == "operator"


async def test_deleting_another_tenants_document_is_404(
    app_session: AppSessionFactory,
) -> None:
    """The RLS fence, at the endpoint. A cross-tenant delete must read as "no
    such document", never as a permission problem that confirms it exists."""
    owner, stranger = uuid.uuid4(), uuid.uuid4()
    async with app_session(owner) as db:
        await _org(db, owner)
        kb = await _kb(db, owner)
        ds = await _source(db, owner)
        kb_id = kb.id
        await _document(db, owner, kb_id=kb_id, ds_id=ds.id, uri="https://x/private")

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            r = await c.request(
                "DELETE",
                f"/api/v1/knowledge/bases/{kb_id}/documents",
                params={"sourceUri": "https://x/private"},
                headers=_headers(stranger),
            )
    assert r.status_code == 404, r.text

    async with app_session(owner) as db:
        assert len(await _live(db, kb_id)) == 1


async def test_a_deleted_source_cannot_be_synced(app_session: AppSessionFactory) -> None:
    """A job queued before the delete, or a redelivered stream entry, must not
    re-ingest into a base the tombstones just landed in."""
    from oc8.knowledge.worker import ingest_job
    from oc8.runtime.queue import RunMessage

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _org(db, tenant)
        kb = await _kb(db, tenant)
        ds = await _source(db, tenant)
        kb_id, ds_id = kb.id, ds.id
        job = m.IngestionJob(tenant_id=tenant, data_source_id=ds_id, kb_id=kb_id, status="queued")
        db.add(job)
        await db.flush()
        job_id = job.id

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            h = _headers(tenant)
            assert (
                await c.delete(f"/api/v1/knowledge/sources/{ds_id}", headers=h)
            ).status_code == 200
            refused = await c.post(
                f"/api/v1/knowledge/sources/{ds_id}/sync", json={"kbId": str(kb_id)}, headers=h
            )
    assert refused.status_code == 404, refused.text

    await ingest_job(
        RunMessage(run_id=str(job_id), tenant_id=str(tenant), entry_id="1-0", redelivered=False)
    )

    async with app_session(tenant) as db:
        done = await db.get(m.IngestionJob, job_id)
        assert done is not None
        assert done.status == "failed"
        assert await _live(db, kb_id) == []


async def test_a_deleted_base_refuses_every_way_of_writing_into_it(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Delete the base" has to be durable, and it was not.

    `sync_source` was hardened for a deleted base; three other doors into the
    same base were not, and measured over HTTP in one session the result was:

        DELETE BASE:              200
        UPLOAD INTO DELETED BASE: 200   ("docs":1,"chunks":1)
        GRANT ON DELETED BASE:    201
        LIST DOCUMENTS:           404
        DELETE AGAIN:             404
        AGENT READS IT:           True

    The agent retrieved the document; the operator could not list it, could not
    delete it and could not see the base at all -- so the only removal path left
    was raw SQL against `kb_chunk`, which is the thing this slice exists to end.
    It was strictly worse than before the slice, where at least the base was
    visible. And the grants `tombstone_base` deletes "because a dangling one is
    an authz hazard" could be re-created on the corpse.
    """
    from oc8.knowledge.connectors import registry
    from oc8.knowledge.retrieval import retrieve_kb_context
    from oc8.knowledge.worker import ingest_job
    from oc8.runtime.queue import RunMessage
    from tests.knowledge.test_tombstone import (
        STUB,
        _FakeEmbedRouter,
        _raw,
        _StubConnector,
    )

    monkeypatch.setattr("oc8.knowledge.ingest.get_model_router", lambda: _FakeEmbedRouter())
    monkeypatch.setattr("oc8.knowledge.retrieval.get_model_router", lambda: _FakeEmbedRouter())
    # A connector that really would deliver a document: with a `website` source
    # and an empty config the sync fails on its own, so the job would read
    # "failed" whether or not the guard exists and the test would prove nothing.
    monkeypatch.setitem(
        registry._CONNECTORS,
        STUB,
        _StubConnector([_raw("test://queued", "the queued document")]),
    )

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _org(db, tenant)
        kb = await _kb(db, tenant)
        ds = await _source(db, tenant)
        ds.connector_type = STUB
        kb_id, ds_id = kb.id, ds.id
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="A")
        db.add(agent)
        await db.flush()
        agent_id = agent.id
        db.add(
            m.KnowledgeGrant(
                tenant_id=tenant, kb_id=kb_id, grantee_type="agent", grantee_id=agent_id
            )
        )
        # Queued BEFORE the delete: the redelivery window the worker owns.
        job = m.IngestionJob(tenant_id=tenant, data_source_id=ds_id, kb_id=kb_id, status="queued")
        db.add(job)
        await db.flush()
        job_id = job.id

    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            h = _headers(tenant)
            assert (
                await c.delete(f"/api/v1/knowledge/bases/{kb_id}", headers=h)
            ).status_code == 200
            upload = await c.post(
                f"/api/v1/knowledge/bases/{kb_id}/documents",
                json={
                    "filename": "after.txt",
                    "content": "the secret merger terms, uploaded after the base was deleted",
                    "contentType": "text/plain",
                },
                headers=h,
            )
            grant = await c.post(
                "/api/v1/knowledge/grants",
                json={
                    "kbId": str(kb_id),
                    "granteeType": "agent",
                    "granteeId": str(agent_id),
                },
                headers=h,
            )
    assert upload.status_code == 404, upload.text
    assert grant.status_code == 404, grant.text

    await ingest_job(
        RunMessage(run_id=str(job_id), tenant_id=str(tenant), entry_id="1-0", redelivered=False)
    )

    async with app_session(tenant) as db:
        done = await db.get(m.IngestionJob, job_id)
        assert done is not None and done.status == "failed"
        assert (done.stats or {}).get("error") == "job has no knowledge base", done.stats
        assert await _live(db, kb_id) == []
        reloaded = await db.get(m.Agent, agent_id)
        assert reloaded is not None
        context, _ = await retrieve_kb_context(
            db, tenant_id=tenant, agent=reloaded, query_text="merger terms"
        )
        assert "merger" not in context, context


async def test_a_base_deleted_between_the_workers_two_transactions_is_refused(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window `ingest_job` opens on purpose, and the only one its second
    check covers.

    `ingest_job` is deliberately two transactions: the `queued -> running` claim
    commits BEFORE the sync loop starts, so a reclaimer sees `running` instead of
    re-running the job. That commit is also a door -- an operator's DELETE of the
    base lands in it, and the sync then writes live chunks into a base nobody can
    list, delete or see. Session 1's check cannot cover this: it passed, and the
    base was alive when it did.

    Measured: with the base check removed from session 2 only, the whole suite
    stayed green, so the second half of the refusal was unpinned. This test is
    the pin -- it deletes the base in exactly that window, through the real
    `tombstone_base`, and asks what the sync did about it.
    """
    from contextlib import asynccontextmanager

    from oc8.knowledge import worker
    from oc8.knowledge.connectors import registry
    from oc8.knowledge.tombstone import tombstone_base
    from oc8.knowledge.worker import ingest_job
    from oc8.runtime.queue import RunMessage
    from tests.knowledge.test_tombstone import (
        STUB,
        _FakeEmbedRouter,
        _raw,
        _StubConnector,
    )

    monkeypatch.setattr("oc8.knowledge.ingest.get_model_router", lambda: _FakeEmbedRouter())
    monkeypatch.setitem(
        registry._CONNECTORS,
        STUB,
        _StubConnector([_raw("test://midflight", "the document the sync would have written")]),
    )

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _org(db, tenant)
        kb = await _kb(db, tenant)
        ds = await _source(db, tenant)
        ds.connector_type = STUB
        kb_id, ds_id = kb.id, ds.id
        job = m.IngestionJob(tenant_id=tenant, data_source_id=ds_id, kb_id=kb_id, status="queued")
        db.add(job)
        await db.flush()
        job_id = job.id

    real = worker.tenant_session
    entered = {"n": 0}

    @asynccontextmanager
    async def interposed(tenant_id: uuid.UUID | None) -> Any:
        entered["n"] += 1
        if entered["n"] == 2:
            # After the claim committed, before the sync's own transaction opens.
            async with real(tenant_id) as other:
                doomed = await other.get(m.KnowledgeBase, kb_id)
                assert doomed is not None
                await tombstone_base(
                    other, tenant_id=tenant, kb=doomed, now=dt.datetime.now(tz=dt.UTC)
                )
        async with real(tenant_id) as session:
            yield session

    monkeypatch.setattr(worker, "tenant_session", interposed)
    await ingest_job(
        RunMessage(run_id=str(job_id), tenant_id=str(tenant), entry_id="1-0", redelivered=False)
    )
    monkeypatch.undo()

    assert entered["n"] >= 2, "the second transaction never opened, so this test proves nothing"
    async with app_session(tenant) as db:
        done = await db.get(m.IngestionJob, job_id)
        assert done is not None
        assert done.status == "failed", done.status
        # Nothing was written into the corpse, by any route.
        rows = (await db.execute(select(m.KbChunk).where(m.KbChunk.kb_id == kb_id))).scalars().all()
        assert [r for r in rows if r.deleted_at is None] == [], [
            (r.source_uri, r.deleted_reason) for r in rows
        ]
        assert rows == [], [(r.source_uri, r.deleted_reason) for r in rows]


# --------------------------------------------------- department cache purge


async def test_an_operator_delete_purges_the_tenants_department_cache(
    app_session: AppSessionFactory, redis_url: str
) -> None:
    """An operator DELETE reduces immediately and irreversibly -- that is the
    GDPR path. A department-cached agent answer generated from the content that
    was just destroyed must not keep being served from Redis for the rest of
    its 24h TTL, and the cache has no per-chunk invalidation, so the whole
    tenant's entries go. Another tenant's entries must survive: the purge is an
    upper bound on this tenant, not a flush of the instance."""
    import redis.asyncio as redis_asyncio

    tenant = uuid.uuid4()
    other_tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _org(db, tenant)
        dept = m.Department(tenant_id=tenant, name="Support", frame={})
        db.add(dept)
        await db.flush()
        kb = await _kb(db, tenant)
        ds = await _source(db, tenant)
        await _document(db, tenant, kb_id=kb.id, ds_id=ds.id, uri="test://doomed")
        kb_id, dept_id = kb.id, dept.id

    mine = f"deptcache:{tenant}:{dept_id}:deadbeef"
    theirs = f"deptcache:{other_tenant}:{uuid.uuid4()}:deadbeef"
    client = redis_asyncio.from_url(redis_url, decode_responses=True)
    try:
        await client.set(mine, "{}", ex=86400)
        await client.set(theirs, "{}", ex=86400)

        app = create_app()
        async with LifespanManager(app):
            async with _client(app) as c:
                r = await c.request(
                    "DELETE",
                    f"/api/v1/knowledge/bases/{kb_id}/documents",
                    params={"sourceUri": "test://doomed"},
                    headers=_headers(tenant),
                )
                assert r.status_code == 200, r.text

        assert await client.get(mine) is None, (
            "the deleting tenant's cached answers must not outlive the content"
        )
        assert await client.get(theirs) is not None, "another tenant's cache must be untouched"
    finally:
        await client.delete(theirs)
        await client.aclose()
