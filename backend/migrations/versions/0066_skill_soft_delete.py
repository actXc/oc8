"""skill soft delete

Revision ID: 0066
Revises: 0065
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("skill", "skill_version", "skill_assignment"):
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
            f"deleted_at TIMESTAMPTZ NULL"
        )


def downgrade() -> None:
    for table in ("skill", "skill_version", "skill_assignment"):
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS deleted_at")
