"""tool_invocation — idempotency for side-effectful tool calls (§8.7 R5)

Prerequisite for a self-driving agent runtime. Without checkpoints a killed
container restarts its task from the beginning, and for an agent that writes to
an external system that means performing the write a second time. This table
lets the repeat return the FIRST result instead of acting again.

Keyed on the TASK rather than the run: a resumed leg continues the same task
under a different run, so the task is the only identity that survives a restart.

Revision ID: 0036
Revises: 0035
Create Date: 2026-07-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_invocation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("args_hash", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id", "task_id", "tool", "args_hash", name="uq_tool_invocation_call"
        ),
    )
    op.create_index("ix_tool_invocation_tenant_id", "tool_invocation", ["tenant_id"])
    op.create_index("ix_tool_invocation_task_id", "tool_invocation", ["task_id"])
    op.execute("ALTER TABLE tool_invocation ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON tool_invocation "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.drop_table("tool_invocation")
