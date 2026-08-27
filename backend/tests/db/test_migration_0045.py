"""0045's backfill: the only provenance it is honest about.

`upload://` is the one URI shape that literally contains its own source uuid, so
it is the one shape the migration can attribute without guessing. The regex
guard is not decoration -- `connectors/upload.py` emits `upload://{filename}`
with no uuid at all, and one such row must not abort the whole upgrade on a cast
error.

The statement is exercised here rather than by cycling the migration itself: the
test database is shared and already at head, and downgrading past 0045 is
documented as a data incident rather than a rollback.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0045_knowledge_deletion.py"
)


def _backfill_sql() -> str:
    spec = importlib.util.spec_from_file_location("oc8_migration_0045", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module._BACKFILL)


async def _chunk(db: Any, tenant: uuid.UUID, kb_id: uuid.UUID, uri: str) -> None:
    db.add(
        m.KbChunk(
            tenant_id=tenant,
            kb_id=kb_id,
            content="body",
            embedding=None,
            source_uri=uri,
            chunk_metadata={"chunk_index": 0},
        )
    )
    await db.flush()


async def test_upload_chunks_are_attributed_and_malformed_uris_are_left_alone(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    source_id = uuid.uuid4()
    async with app_session(tenant) as db:
        kb = m.KnowledgeBase(tenant_id=tenant, name="KB", embedding_model="nomic-embed-text")
        db.add(kb)
        await db.flush()
        await _chunk(db, tenant, kb.id, f"upload://{source_id}/vertrag.pdf")
        await _chunk(db, tenant, kb.id, "upload://loose.pdf")
        await _chunk(db, tenant, kb.id, "https://example.com/page")

        await db.execute(text(_backfill_sql()))

        rows = {
            uri: ds
            for uri, ds in (
                await db.execute(
                    select(m.KbChunk.source_uri, m.KbChunk.data_source_id).where(
                        m.KbChunk.kb_id == kb.id
                    )
                )
            ).all()
        }
        assert rows[f"upload://{source_id}/vertrag.pdf"] == source_id
        assert rows["upload://loose.pdf"] is None, "no uuid in the URI, so nothing to attribute"
        assert rows["https://example.com/page"] is None, (
            "a connector chunk carries no source identity; guessing it is the exact failure "
            "data_source_id exists to prevent"
        )
