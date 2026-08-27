"""agent_run table with RLS

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 is pinned to its own frozen table snapshot and does NOT create
    # `agent_run`, so this revision is its sole creator on every history (fresh
    # install and incremental deploy alike). Column defaults mirror the ORM model
    # (`oc8.models.run.AgentRun`): Python-side defaults only, so no server_default
    # on state/cursor/messages/context; only the timestamps carry a DB default,
    # matching TimestampMixin's server_default=now().
    op.create_table(
        "agent_run",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=True),
        sa.Column("cursor", JSONB(), nullable=False),
        sa.Column("messages", JSONB(), nullable=False),
        sa.Column("context", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('queued','running','waiting_for_input',"
            "'waiting_for_approval','failed','done')",
            name="ck_agent_run_state",
        ),
    )
    op.create_index("ix_agent_run_tenant_id", "agent_run", ["tenant_id"])
    op.create_index("ix_agent_run_agent_id", "agent_run", ["agent_id"])
    op.execute("ALTER TABLE agent_run ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON agent_run "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.drop_table("agent_run")
