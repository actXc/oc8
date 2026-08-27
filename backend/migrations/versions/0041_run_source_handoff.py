"""agent_run.source may say 'handoff'

A run started because another department handed work over is not a cron tick,
not an operator, and not a delegation from a lead — it is the thing that CAUSES
the delegation. Reusing 'event' for it would have saved a migration and lost the
origin in every audit query afterwards.

Revision ID: 0041
Revises: 0040
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCES = "'manual','cron','event','delegation','decision'"


def upgrade() -> None:
    op.execute("ALTER TABLE agent_run DROP CONSTRAINT IF EXISTS ck_agent_run_source")
    op.execute(
        "ALTER TABLE agent_run ADD CONSTRAINT ck_agent_run_source "
        f"CHECK (source = ANY (ARRAY[{_SOURCES},'handoff']))"
    )


def downgrade() -> None:
    op.execute("UPDATE agent_run SET source = 'event' WHERE source = 'handoff'")
    op.execute("ALTER TABLE agent_run DROP CONSTRAINT IF EXISTS ck_agent_run_source")
    op.execute(
        "ALTER TABLE agent_run ADD CONSTRAINT ck_agent_run_source "
        f"CHECK (source = ANY (ARRAY[{_SOURCES}]))"
    )
