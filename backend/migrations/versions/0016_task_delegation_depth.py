"""task.delegation_depth + widen agent_run.source for delegation (§7)

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `task` IS in 0001_initial's frozen create_all() table set, so a fresh DB
    # already has delegation_depth from the live Task model; a DB migrated
    # through 0015 needs the explicit ALTER. IF NOT EXISTS converges both.
    # Same hazard, same guard as migration 0010.
    op.execute(
        "ALTER TABLE task ADD COLUMN IF NOT EXISTS delegation_depth integer NOT NULL DEFAULT 0"
    )
    # `agent_run` is NOT in 0001's create_all set (migration 0002 creates it
    # explicitly, 0014 added this CHECK), so migrations are its sole schema
    # source -- a plain drop+recreate is correct on every path. IF EXISTS is
    # defensive only, matching migration 0011's ck_task_state widen style.
    op.execute("ALTER TABLE agent_run DROP CONSTRAINT IF EXISTS ck_agent_run_source")
    op.execute(
        "ALTER TABLE agent_run ADD CONSTRAINT ck_agent_run_source "
        "CHECK (source IN ('manual','cron','event','delegation'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent_run DROP CONSTRAINT IF EXISTS ck_agent_run_source")
    op.execute(
        "ALTER TABLE agent_run ADD CONSTRAINT ck_agent_run_source "
        "CHECK (source IN ('manual','cron','event'))"
    )
    op.execute("ALTER TABLE task DROP COLUMN IF EXISTS delegation_depth")
