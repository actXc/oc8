"""flow + flow_version + flow_run tables with RLS

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007"
down_revision: str | None = "0006"
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
        "flow",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.UniqueConstraint("tenant_id", "name", name="uq_flow_tenant_name"),
    )
    op.create_index("ix_flow_tenant_id", "flow", ["tenant_id"])
    op.create_index("ix_flow_name", "flow", ["name"])
    _rls("flow")

    op.create_table(
        "flow_version",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("flow_id", sa.Uuid(), nullable=False),
        sa.Column("semver", sa.Text(), nullable=False),
        sa.Column("spec", JSONB(), nullable=False),
        sa.Column("artifact_hash", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.UniqueConstraint("flow_id", "semver", name="uq_flow_version"),
    )
    op.create_index("ix_flow_version_tenant_id", "flow_version", ["tenant_id"])
    op.create_index("ix_flow_version_flow_id", "flow_version", ["flow_id"])
    _rls("flow_version")

    op.create_table(
        "flow_run",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("flow_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("current_stages", JSONB(), nullable=False, server_default="[]"),
        sa.Column("context", JSONB(), nullable=False, server_default="{}"),
        sa.Column("trigger_event", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.CheckConstraint(
            "status IN ('running','completed','failed','suspended')", name="ck_flow_run_status"
        ),
    )
    op.create_index("ix_flow_run_tenant_id", "flow_run", ["tenant_id"])
    op.create_index("ix_flow_run_flow_version_id", "flow_run", ["flow_version_id"])
    _rls("flow_run")


def downgrade() -> None:
    op.drop_table("flow_run")
    op.drop_table("flow_version")
    op.drop_table("flow")
