"""plugin + plugin_version + plugin_installation tables with RLS; drop agent_module*

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0009"
down_revision: str | None = "0008"
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
        "plugin",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=False, server_default=""),
        sa.Column("origin", sa.Text(), nullable=False, server_default="local"),
        sa.Column("trust_level", sa.Text(), nullable=False, server_default="first_party"),
        sa.Column("core_compat", sa.Text(), nullable=False, server_default=""),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.CheckConstraint(
            "type IN ('skill','flow_template','agent_template','department_template',"
            "'connector','model_adapter','runtime_adapter','tool_pack','core_extension')",
            name="ck_plugin_type",
        ),
        sa.CheckConstraint("origin IN ('local','store')", name="ck_plugin_origin"),
        sa.CheckConstraint(
            "trust_level IN ('first_party','verified','community')", name="ck_plugin_trust"
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_plugin_tenant_name"),
    )
    op.create_index("ix_plugin_tenant_id", "plugin", ["tenant_id"])
    op.create_index("ix_plugin_name", "plugin", ["name"])
    _rls("plugin")

    op.create_table(
        "plugin_version",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("plugin_id", sa.Uuid(), nullable=False),
        sa.Column("semver", sa.Text(), nullable=False),
        sa.Column("manifest", JSONB(), nullable=False),
        sa.Column("artifact_hash", sa.LargeBinary(), nullable=False),
        sa.Column("permissions", JSONB(), nullable=False, server_default="[]"),
        sa.Column("capabilities", JSONB(), nullable=False, server_default="[]"),
        sa.Column("entry_points", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.UniqueConstraint("plugin_id", "semver", name="uq_plugin_version"),
    )
    op.create_index("ix_plugin_version_tenant_id", "plugin_version", ["tenant_id"])
    op.create_index("ix_plugin_version_plugin_id", "plugin_version", ["plugin_id"])
    _rls("plugin_version")

    op.create_table(
        "plugin_installation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("plugin_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="installed"),
        sa.Column("granted_permissions", JSONB(), nullable=False, server_default="[]"),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_reason", sa.Text(), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.CheckConstraint(
            "status IN ('installed','enabled','disabled','quarantined')",
            name="ck_plugin_installation_status",
        ),
        sa.UniqueConstraint("tenant_id", "plugin_id", name="uq_plugin_installation"),
    )
    op.create_index("ix_plugin_installation_tenant_id", "plugin_installation", ["tenant_id"])
    op.create_index("ix_plugin_installation_plugin_id", "plugin_installation", ["plugin_id"])
    _rls("plugin_installation")

    op.drop_table("agent_module_version")
    op.drop_table("agent_module")


def downgrade() -> None:  # pragma: no cover - forward-only in practice
    raise NotImplementedError("0009 is forward-only")
