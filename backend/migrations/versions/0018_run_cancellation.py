"""run_cancellation table (§7.2 cancel signal) + drop agent_run.cancel_requested_at

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # New table (not in 0001's create_all frozen set) -> it adds its own RLS
    # policy, mirroring migration 0003's clarification table.
    op.create_table(
        "run_cancellation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "cancellation_kind",
            sa.Text(),
            nullable=False,
            server_default="operator_interrupted",
        ),
    )
    op.create_index("ix_run_cancellation_tenant_id", "run_cancellation", ["tenant_id"])
    op.create_index(
        "ix_run_cancellation_run_id", "run_cancellation", ["run_id"], unique=True
    )
    op.execute("ALTER TABLE run_cancellation ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON run_cancellation "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    # Drop the vestigial signal column added by 0017: the run_cancellation table
    # replaces it (writing agent_run for a running run deadlocks on its own lock).
    op.execute("ALTER TABLE agent_run DROP COLUMN IF EXISTS cancel_requested_at")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE agent_run ADD COLUMN IF NOT EXISTS cancel_requested_at timestamptz"
    )
    op.drop_table("run_cancellation")
