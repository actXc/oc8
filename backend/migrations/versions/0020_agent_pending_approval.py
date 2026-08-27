"""agent.status 'pending_approval' (A4 §5.5 hire-approval gate)

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `agent` is in migration 0001's create_all frozen set, so a fresh DB gets the
    # widened CHECK from the model; a migrated DB has the six-value CHECK. Plain
    # drop+recreate converges both (mirrors migration 0011's ck_task_state widen).
    op.execute("ALTER TABLE agent DROP CONSTRAINT IF EXISTS ck_agent_status")
    op.execute(
        "ALTER TABLE agent ADD CONSTRAINT ck_agent_status "
        "CHECK (status IN ('running','idle','waiting_for_approval','paused',"
        "'error','stopped','pending_approval'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent DROP CONSTRAINT IF EXISTS ck_agent_status")
    op.execute(
        "ALTER TABLE agent ADD CONSTRAINT ck_agent_status "
        "CHECK (status IN ('running','idle','waiting_for_approval','paused',"
        "'error','stopped'))"
    )
