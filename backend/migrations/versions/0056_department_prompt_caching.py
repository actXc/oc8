"""Add department.prompt_caching_enabled and token_usage_record's cache/savings columns.

`department` and `token_usage_record` are both in 0001_initial.py's
_TABLES_AT_0001, created live from the current SQLAlchemy model state -- so a
fresh test database already has these columns by the time this migration
runs. IF NOT EXISTS / IF EXISTS guard both directions, same pattern as 0055
(model_config.used_by_copilot) and 0054 (agent.config_revision) before it.

Revision ID: 0056
Revises: 0055
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE department ADD COLUMN IF NOT EXISTS "
        "prompt_caching_enabled boolean NOT NULL DEFAULT true"
    )
    op.execute(
        "ALTER TABLE token_usage_record ADD COLUMN IF NOT EXISTS "
        "cache_hit boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE token_usage_record ADD COLUMN IF NOT EXISTS "
        "saved_tokens_in bigint NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE token_usage_record ADD COLUMN IF NOT EXISTS "
        "saved_tokens_out bigint NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE token_usage_record ADD COLUMN IF NOT EXISTS "
        "saved_cost_micros bigint NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE department DROP COLUMN IF EXISTS prompt_caching_enabled")
    op.execute("ALTER TABLE token_usage_record DROP COLUMN IF EXISTS cache_hit")
    op.execute("ALTER TABLE token_usage_record DROP COLUMN IF EXISTS saved_tokens_in")
    op.execute("ALTER TABLE token_usage_record DROP COLUMN IF EXISTS saved_tokens_out")
    op.execute("ALTER TABLE token_usage_record DROP COLUMN IF EXISTS saved_cost_micros")
