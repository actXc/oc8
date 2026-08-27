"""budget hard-stop: agent.pause_reason/paused_at + budget.override_until (A2 §15.4)

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # agent is in migration 0001's create_all frozen set, so the model declares
    # these columns and a fresh DB already has them -- IF NOT EXISTS converges the
    # migrated-DB path with the fresh-DB path.
    op.execute("ALTER TABLE agent ADD COLUMN IF NOT EXISTS pause_reason text")
    op.execute("ALTER TABLE agent ADD COLUMN IF NOT EXISTS paused_at timestamptz")
    # budget is created explicitly by migration 0011 (not in the 0001 set), so on
    # a fresh DB the column is genuinely added here; IF NOT EXISTS is defensive.
    op.execute("ALTER TABLE budget ADD COLUMN IF NOT EXISTS override_until timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE budget DROP COLUMN IF EXISTS override_until")
    op.execute("ALTER TABLE agent DROP COLUMN IF EXISTS paused_at")
    op.execute("ALTER TABLE agent DROP COLUMN IF EXISTS pause_reason")
