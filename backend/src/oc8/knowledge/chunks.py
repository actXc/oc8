"""Where every read of `kb_chunk` starts.

A tombstoned chunk is a record that a document was removed, not a document. The
predicate lives in one function rather than in each caller because a read path
that forgets it is indistinguishable from never having deleted anything -- and
it fails silently: the deletion still LOOKS done everywhere an operator would
check, while the agent keeps answering from the text.

`tests/knowledge/test_read_path_guard.py` makes that structural rather than a
convention: `m.KbChunk` may only be named inside an allowlisted set of modules,
so a new read path fails the build instead of being caught in review.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m


def live_chunks() -> Select[tuple[m.KbChunk]]:
    """Chunks that are still answerable. Narrow this; do not rebuild it."""
    return select(m.KbChunk).where(m.KbChunk.deleted_at.is_(None))


async def linked_source_ids(db: AsyncSession) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Which sources currently feed each base, one entry per (kb_id, source)
    pair with at least one live chunk. `KnowledgeBase.source_ids` itself is
    never written anywhere in this codebase (create_base doesn't set it, no
    sync path updates it), so it would always report the create-time default
    regardless of what has actually been synced in -- this is the real
    answer. The predicate is spelled out rather than reusing `live_chunks()`,
    for the same reason as `list_documents`: this is a projection over
    columns, not a select of the entity."""
    rows = (
        await db.execute(
            select(m.KbChunk.kb_id, m.KbChunk.data_source_id)
            .where(m.KbChunk.data_source_id.is_not(None), m.KbChunk.deleted_at.is_(None))
            .distinct()
        )
    ).all()
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    for kb_id, source_id in rows:
        out.setdefault(kb_id, []).append(source_id)
    return out


@dataclass(frozen=True)
class DocumentSummary:
    """One document of a knowledge base, as its chunks add up to it.

    A document is not a row anywhere -- it is a `(data_source_id, source_uri)`
    group of chunks -- so this is what the listing route has to hand back.
    """

    source_uri: str
    data_source_id: uuid.UUID | None
    kb_id: uuid.UUID
    chunks: int
    created_at: dt.datetime | None
    deleted_at: dt.datetime | None
    deleted_reason: str | None
    reduced_at: dt.datetime | None


async def list_documents(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kb_id: uuid.UUID,
    include_deleted: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[DocumentSummary]:
    """What a knowledge base holds, one row per document.

    This lives here rather than in the route because `api/v1/knowledge.py` is not
    on the read path's allowlist -- and it is not optional: `source_uri` appears
    in no DTO, no response and no frontend file today, so without a way to
    enumerate documents the DELETE route would ship addressable by nobody. For an
    upload the URI is `upload://{server-generated-uuid}/{filename}`, which no
    human can be expected to reconstruct.

    The `deleted_at` predicate is spelled out rather than reusing `live_chunks()`
    because this is an aggregate over columns, not a select of the entity.

    A document is a group of chunks and a group can span GENERATIONS, so
    `max(deleted_at)` over the whole group was measured to describe the wrong
    one: since amendment A3 every re-ingested document carries a superseded
    predecessor, and `?includeDeleted=true` therefore reported the CURRENT,
    perfectly live document as `deleted/superseded` with twice its real chunk
    count -- which is how an operator was led to click restore on a document that
    had never gone anywhere. A group with a live chunk in it is a live document:
    it reports its live chunk count and no deletion at all, and the tombstone
    columns describe only groups where nothing answers any more.
    """
    live = func.count().filter(m.KbChunk.deleted_at.is_(None)).label("live_chunks")
    grouped = (
        select(
            m.KbChunk.source_uri,
            m.KbChunk.data_source_id,
            live,
            func.count().label("chunks"),
            func.min(m.KbChunk.created_at).label("created_at"),
            func.max(m.KbChunk.deleted_at).label("deleted_at"),
            func.max(m.KbChunk.deleted_reason).label("deleted_reason"),
            func.max(m.KbChunk.reduced_at).label("reduced_at"),
        )
        .where(m.KbChunk.tenant_id == tenant_id, m.KbChunk.kb_id == kb_id)
        .group_by(m.KbChunk.source_uri, m.KbChunk.data_source_id)
        # Oldest first, then by URI: a listing whose order changes between two
        # calls makes `offset` meaningless and the second page unreliable.
        .order_by(func.min(m.KbChunk.created_at), m.KbChunk.source_uri)
        .limit(limit)
        .offset(offset)
    )
    if not include_deleted:
        grouped = grouped.where(m.KbChunk.deleted_at.is_(None))

    return [
        DocumentSummary(
            source_uri=row.source_uri,
            data_source_id=row.data_source_id,
            kb_id=kb_id,
            chunks=int(row.live_chunks) if row.live_chunks else int(row.chunks),
            created_at=row.created_at,
            deleted_at=None if row.live_chunks else row.deleted_at,
            deleted_reason=None if row.live_chunks else row.deleted_reason,
            reduced_at=None if row.live_chunks else row.reduced_at,
        )
        for row in (await db.execute(grouped)).all()
    ]


@dataclass(frozen=True)
class ChunkRow:
    """A plain, non-ORM view of one live chunk -- the read-path guard forbids
    `KbChunk` itself from leaving this module, so this is what the API layer
    and serializers work with instead."""

    id: uuid.UUID
    kb_id: uuid.UUID
    source_uri: str
    content: str
    classification: str
    chunk_metadata: dict[str, Any]
    created_at: dt.datetime


async def list_chunks(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kb_id: uuid.UUID,
    search: str | None = None,
    source_uri: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[ChunkRow], int]:
    """Live chunks in one KB, newest-first, optionally filtered by document
    (`source_uri`) or a case-insensitive substring of `content`."""
    stmt = live_chunks().where(m.KbChunk.tenant_id == tenant_id, m.KbChunk.kb_id == kb_id)
    if source_uri:
        stmt = stmt.where(m.KbChunk.source_uri == source_uri)
    if search:
        stmt = stmt.where(m.KbChunk.content.ilike(f"%{search}%"))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    stmt = stmt.order_by(m.KbChunk.created_at.desc(), m.KbChunk.id).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        ChunkRow(
            id=c.id,
            kb_id=c.kb_id,
            source_uri=c.source_uri,
            content=c.content,
            classification=c.classification,
            chunk_metadata=c.chunk_metadata,
            created_at=c.created_at,
        )
        for c in rows
    ], total
