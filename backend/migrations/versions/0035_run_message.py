"""run_message table — operator->agent live steering (chat during a run)

A separate, append-only, RLS-scoped table, mirroring run_cancellation (0018):
the executor holds a long lock on the agent_run row, so an operator message is
written here by the endpoint and observed by the running executor under READ
COMMITTED at the next step boundary. `delivered` flips once injected.

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_message",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False, server_default="operator"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_run_message_tenant_id", "run_message", ["tenant_id"])
    op.create_index("ix_run_message_run_id", "run_message", ["run_id"])
    op.execute("ALTER TABLE run_message ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON run_message "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.drop_table("run_message")
