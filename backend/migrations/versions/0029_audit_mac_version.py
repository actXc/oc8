"""audit_event.mac_version (§12.5 keyed chain)

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # audit_event predates the model-driven create_all in 0001's frozen set, so
    # add the column with raw DDL and an IF NOT EXISTS guard, matching how
    # migration 0021 added responsible_type/responsible_id to this same table.
    op.execute(
        "ALTER TABLE audit_event ADD COLUMN IF NOT EXISTS mac_version smallint NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE audit_event DROP COLUMN IF EXISTS mac_version")
