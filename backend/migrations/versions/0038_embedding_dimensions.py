"""kb_chunk / memory_record embeddings: match the configured embedding model

EMBED_DIM was 1536 -- the size OpenAI's embeddings produce -- while the default
embedding model has long been `nomic-embed-text`, which produces 768. Nothing
could be ingested at all: every insert died with `expected 1536 dimensions, not
768` from the driver, surfaced as a bare 500. The knowledge base was unusable in
the default configuration, and no test caught it because tests never embed.

Both tables are empty of real vectors here, so this is a plain type change. A
deployment that DOES hold 1536-dimension vectors from an OpenAI-sized model must
re-embed rather than run this blindly -- which is why the dimension now sits next
to the model setting it belongs to.

Revision ID: 0038
Revises: 0037
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("kb_chunk", "memory_record"):
        op.execute(f"UPDATE {table} SET embedding = NULL WHERE embedding IS NOT NULL")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN embedding TYPE vector(768)")


def downgrade() -> None:
    for table in ("kb_chunk", "memory_record"):
        op.execute(f"UPDATE {table} SET embedding = NULL WHERE embedding IS NOT NULL")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN embedding TYPE vector(1536)")
