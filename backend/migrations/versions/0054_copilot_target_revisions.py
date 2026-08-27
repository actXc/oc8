"""Atomically bump Copilot target revisions on every configuration update.

Revision ID: 0054
Revises: 0053
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("agent", "plugin", "integration")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
            "config_revision integer NOT NULL DEFAULT 1"
        )
    op.execute("""
        CREATE OR REPLACE FUNCTION oc8_bump_copilot_config_revision()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          NEW.config_revision := OLD.config_revision + 1;
          RETURN NEW;
        END;
        $$
    """)
    for table in _TABLES:
        trigger = f"trg_{table}_copilot_config_revision"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION oc8_bump_copilot_config_revision()"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_copilot_config_revision ON {table}")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS config_revision")
    op.execute("DROP FUNCTION IF EXISTS oc8_bump_copilot_config_revision()")
