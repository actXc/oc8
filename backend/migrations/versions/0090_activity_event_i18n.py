"""activity_event.i18n -- locale overlays for seeded demo activity rows.

Revision ID: 0090
Revises: 0089
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0090"
down_revision: str | None = "0089"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # activity_event is in migration 0001's create_all frozen set: the model
    # declares i18n now, so a fresh DB already has the column by the time this
    # migration runs. IF NOT EXISTS converges the fresh-install path with the
    # incremental-migration path.
    op.execute(
        "ALTER TABLE activity_event ADD COLUMN IF NOT EXISTS "
        "i18n jsonb NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE activity_event DROP COLUMN IF EXISTS i18n")
