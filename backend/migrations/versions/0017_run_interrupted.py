"""agent_run.state 'interrupted' + cancel_requested_at (§7.2 run cancel)

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # agent_run is created explicitly by migration 0002 (NOT in 0001's create_all
    # frozen set), so migrations are its sole schema source -- a plain drop+recreate
    # of the CHECK is correct on every path. IF EXISTS is defensive only, matching
    # migration 0011's ck_task_state widen style.
    op.execute("ALTER TABLE agent_run DROP CONSTRAINT IF EXISTS ck_agent_run_state")
    op.execute(
        "ALTER TABLE agent_run ADD CONSTRAINT ck_agent_run_state "
        "CHECK (state IN ('queued','running','waiting_for_input',"
        "'waiting_for_approval','failed','done','interrupted'))"
    )
    # The column is declared on the AgentRun model, so a fresh DB bootstrapping
    # through migration 0002's create_table already has it; a DB migrated through
    # 0016 needs the ALTER. IF NOT EXISTS converges both.
    op.execute(
        "ALTER TABLE agent_run ADD COLUMN IF NOT EXISTS cancel_requested_at "
        "timestamptz"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent_run DROP COLUMN IF EXISTS cancel_requested_at")
    op.execute("ALTER TABLE agent_run DROP CONSTRAINT IF EXISTS ck_agent_run_state")
    op.execute(
        "ALTER TABLE agent_run ADD CONSTRAINT ck_agent_run_state "
        "CHECK (state IN ('queued','running','waiting_for_input',"
        "'waiting_for_approval','failed','done'))"
    )
