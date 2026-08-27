"""activity_event gains cache_hit -- lets an operator watching the Activity
Feed tell a genuinely fresh model answer apart from one replayed from the
department response cache (oc8.agent.cache_flow), instead of that
distinction only existing on TokenUsageRecord.cache_hit, which the feed
never reads.

Revision ID: 0075
Revises: 0074
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0075"
down_revision: str | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # activity_event is in migration 0001's create_all frozen set: the model
    # declares cache_hit now, so a fresh DB already has the column by the
    # time this migration runs. IF NOT EXISTS converges the fresh-install
    # path with the incremental-migration path. NOT NULL needs a real
    # server_default, not just the ORM-side default=False, or this ALTER
    # fails on any tenant with existing rows.
    op.execute(
        "ALTER TABLE activity_event ADD COLUMN IF NOT EXISTS "
        "cache_hit boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE activity_event DROP COLUMN IF EXISTS cache_hit")
