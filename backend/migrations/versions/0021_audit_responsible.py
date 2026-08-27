"""audit_event responsible_type/responsible_id (A3 §12.5 write-time attribution)

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # audit_event is in migration 0001's create_all frozen set, so the model
    # declares these and a fresh DB already has them; IF NOT EXISTS converges the
    # migrated-DB path. Forward-only: pre-A3 rows keep NULL (no backfill).
    # DO NOT EVER backfill responsible_* onto pre-0021 rows: their hash was
    # computed WITHOUT these keys (append_event omits them when NULL), so setting
    # them makes verify_chain reconstruct a different payload and FAILS the hash
    # chain for every backfilled row.
    op.execute("ALTER TABLE audit_event ADD COLUMN IF NOT EXISTS responsible_type text")
    op.execute("ALTER TABLE audit_event ADD COLUMN IF NOT EXISTS responsible_id text")


def downgrade() -> None:
    op.execute("ALTER TABLE audit_event DROP COLUMN IF EXISTS responsible_id")
    op.execute("ALTER TABLE audit_event DROP COLUMN IF EXISTS responsible_type")
