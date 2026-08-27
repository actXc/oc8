"""approval_request.decision_option — which of the agent's options a human picked

An agent that hits something it may not decide alone raises an approval carrying
its own proposed OPTIONS. Recording only "approved" would lose which one was
chosen, so the follow-up run could not act on it and the inbox could not show
what was actually decided afterwards.

Nullable, and no backfill: every existing approval is a plain yes/no on a held
tool call and has no options to have chosen from.

Revision ID: 0037
Revises: 0036
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IF NOT EXISTS, because a test database is built from the LIVE ORM models
    # and already has the column, while a real deployment does not. Documented
    # hazard in this repo -- see 0010 and 0021 for the same guard.
    op.execute("ALTER TABLE approval_request ADD COLUMN IF NOT EXISTS decision_option text")
    # A decided decision produces a run, and `source` is a closed CHECK: without
    # widening it the follow-up run cannot be written at all and the whole loop
    # dies at the last step. Drop+recreate is how 0016 widened the same one.
    op.execute("ALTER TABLE agent_run DROP CONSTRAINT IF EXISTS ck_agent_run_source")
    op.execute(
        "ALTER TABLE agent_run ADD CONSTRAINT ck_agent_run_source "
        "CHECK (source IN ('manual','cron','event','delegation','decision'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent_run DROP CONSTRAINT IF EXISTS ck_agent_run_source")
    op.execute(
        "ALTER TABLE agent_run ADD CONSTRAINT ck_agent_run_source "
        "CHECK (source IN ('manual','cron','event','delegation'))"
    )
    op.drop_column("approval_request", "decision_option")
