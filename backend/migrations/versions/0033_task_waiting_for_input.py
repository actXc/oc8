"""task.state 'waiting_for_input' (clarification loop, §ask_user)

run_agent (Task 1 of the clarification-loop plan) sets Task.state =
"waiting_for_input" when an agent calls ask_user with a non-empty question.
ck_task_state didn't allow that value, so the transition failed the CHECK.
Widen it, mirroring migration 0011's ck_task_state widen style (plain
drop+recreate: `task` is in migration 0001's create_all frozen set, so a fresh
DB already has the widened CHECK from the model while a migrated DB has the
narrower one -- drop+recreate converges both).

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE task DROP CONSTRAINT IF EXISTS ck_task_state")
    op.execute(
        "ALTER TABLE task ADD CONSTRAINT ck_task_state "
        "CHECK (state IN ('backlog','in_progress','waiting_for_approval',"
        "'waiting_for_input','done','failed','budget_exceeded'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE task DROP CONSTRAINT IF EXISTS ck_task_state")
    op.execute(
        "ALTER TABLE task ADD CONSTRAINT ck_task_state "
        "CHECK (state IN ('backlog','in_progress','waiting_for_approval','done','failed',"
        "'budget_exceeded'))"
    )
