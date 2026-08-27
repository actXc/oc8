"""Drop TokenUsageRecord.provider_cost_micros / saved_cost_micros -- cost is
now computed lazily from tokens x the versioned model_price table (0060),
never stored (Cost Center design, 2026-08-19 spec Part A).

`token_usage_record` is in 0001_initial.py's `_TABLES_AT_0001` set, created live
from the current SQLAlchemy model state -- so on a fresh test database this
migration runs against a table that never had these columns to begin with (the
model no longer declares them). IF EXISTS / IF NOT EXISTS guard both directions,
same pattern as 0056 (department.prompt_caching_enabled / token_usage_record's
cache/savings columns) before it.

Revision ID: 0061
Revises: 0060
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE token_usage_record DROP COLUMN IF EXISTS provider_cost_micros")
    op.execute("ALTER TABLE token_usage_record DROP COLUMN IF EXISTS saved_cost_micros")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE token_usage_record ADD COLUMN IF NOT EXISTS "
        "provider_cost_micros bigint NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE token_usage_record ADD COLUMN IF NOT EXISTS "
        "saved_cost_micros bigint NOT NULL DEFAULT 0"
    )
