"""KPI infrastructure: run_state_transition logs every AgentRun state
change so per-state durations (total wall-clock, pure execution time,
time-in-approval-wait) can be computed from history instead of guessed
from created_at/updated_at alone.

Revision ID: 0080
Revises: 0079
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0080"
down_revision: str | None = "0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = dict(server_default=sa.func.now(), nullable=False)


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def upgrade() -> None:
    op.create_table(
        "run_state_transition",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("from_state", sa.Text(), nullable=True),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), **_TS),
        sa.CheckConstraint(
            "from_state IS NULL OR from_state IN "
            "('queued','running','waiting_for_input','waiting_for_approval',"
            "'failed','done','interrupted')",
            name="ck_run_state_transition_from_state",
        ),
        sa.CheckConstraint(
            "to_state IN "
            "('queued','running','waiting_for_input','waiting_for_approval',"
            "'failed','done','interrupted')",
            name="ck_run_state_transition_to_state",
        ),
    )
    op.create_index(
        "ix_run_state_transition_run_at", "run_state_transition", ["tenant_id", "run_id", "at"]
    )
    op.create_index(
        "ix_run_state_transition_state_at",
        "run_state_transition",
        ["tenant_id", "to_state", "at"],
    )
    _rls("run_state_transition")


def downgrade() -> None:
    op.drop_table("run_state_transition")
