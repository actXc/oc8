"""agent_module + agent_module_version tables with RLS

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004"
down_revision: str | None = "0003"
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
        "agent_module",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("trust_level", sa.Text(), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.CheckConstraint("origin IN ('local','store')", name="ck_agent_module_origin"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_agent_module_tenant_name"),
    )
    op.create_index("ix_agent_module_tenant_id", "agent_module", ["tenant_id"])
    op.create_index("ix_agent_module_name", "agent_module", ["name"])
    _rls("agent_module")

    op.create_table(
        "agent_module_version",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("module_id", sa.Uuid(), nullable=False),
        sa.Column("semver", sa.Text(), nullable=False),
        sa.Column("manifest", JSONB(), nullable=False),
        sa.Column("artifact_hash", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.UniqueConstraint("module_id", "semver", name="uq_agent_module_version"),
    )
    op.create_index("ix_agent_module_version_tenant_id", "agent_module_version", ["tenant_id"])
    op.create_index("ix_agent_module_version_module_id", "agent_module_version", ["module_id"])
    _rls("agent_module_version")


def downgrade() -> None:
    op.drop_table("agent_module_version")
    op.drop_table("agent_module")
