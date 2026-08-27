"""handoff_type + handoff tables with RLS

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = dict(server_default=sa.func.now(), nullable=False)


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def upgrade() -> None:
    op.create_table(
        "handoff_type",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("payload_schema", JSONB(), nullable=False),
        sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.UniqueConstraint("tenant_id", "name", name="uq_handoff_type_tenant_name"),
    )
    op.create_index("ix_handoff_type_tenant_id", "handoff_type", ["tenant_id"])
    _rls("handoff_type")

    op.create_table(
        "handoff",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("handoff_type_id", sa.Uuid(), nullable=False),
        sa.Column("source_department_id", sa.Uuid(), nullable=False),
        sa.Column("target_department_id", sa.Uuid(), nullable=False),
        sa.Column("source_task_id", sa.Uuid(), nullable=True),
        sa.Column("target_task_id", sa.Uuid(), nullable=True),
        sa.Column("flow_run_id", sa.Uuid(), nullable=True),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("attachments", JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("gate", sa.Text(), nullable=False, server_default="auto"),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.CheckConstraint(
            "status IN ('pending','accepted','in_progress','completed','rejected','expired')",
            name="ck_handoff_status",
        ),
        sa.CheckConstraint("gate IN ('auto','approval')", name="ck_handoff_gate"),
    )
    op.create_index("ix_handoff_tenant_id", "handoff", ["tenant_id"])
    op.create_index("ix_handoff_handoff_type_id", "handoff", ["handoff_type_id"])
    op.create_index("ix_handoff_source_department_id", "handoff", ["source_department_id"])
    op.create_index("ix_handoff_target_department_id", "handoff", ["target_department_id"])
    _rls("handoff")


def downgrade() -> None:
    op.drop_table("handoff")
    op.drop_table("handoff_type")
