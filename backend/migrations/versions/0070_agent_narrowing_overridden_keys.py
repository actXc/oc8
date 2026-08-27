"""agent gains narrowing_overridden_keys -- backend-only operator-override
provenance tracking (agent tool login selection design, Task 5 fix round 2)

Revision ID: 0070
Revises: 0069
Create Date: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # agent is in migration 0001's create_all frozen set (cf. 0034/0069): the
    # model declares narrowing_overridden_keys now, so a fresh DB already has
    # the column by the time this migration runs. IF NOT EXISTS converges the
    # fresh-install path with the incremental-migration path, same pattern as
    # 0069's credential_id column.
    op.execute(
        "ALTER TABLE agent ADD COLUMN IF NOT EXISTS narrowing_overridden_keys "
        "jsonb NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent DROP COLUMN IF EXISTS narrowing_overridden_keys")
