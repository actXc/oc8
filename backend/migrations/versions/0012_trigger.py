"""trigger table for cron schedules + event subscriptions (§8.4)

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = dict(server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "trigger",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("task_text", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("cron_expression", sa.Text(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_source", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.CheckConstraint("kind IN ('cron','event')", name="ck_trigger_kind"),
        sa.CheckConstraint(
            "(kind = 'cron' AND cron_expression IS NOT NULL "
            "AND event_source IS NULL AND event_type IS NULL) "
            "OR (kind = 'event' AND event_source IS NOT NULL AND event_type IS NOT NULL "
            "AND cron_expression IS NULL)",
            name="ck_trigger_kind_fields",
        ),
    )
    op.create_index("ix_trigger_tenant_id", "trigger", ["tenant_id"])
    op.create_index("ix_trigger_agent_id", "trigger", ["agent_id"])
    op.execute("ALTER TABLE trigger ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON trigger "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.drop_table("trigger")
