"""component_grant table with RLS (governed components, design doc
2026-08-21-governed-components)

Revision ID: 0068
Revises: 0067
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0068"
down_revision: str | None = "0067"
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
        "component_grant",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("component_key", sa.Text(), nullable=False),
        sa.Column("grantee_type", sa.Text(), nullable=False),
        sa.Column("grantee_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.CheckConstraint(
            "grantee_type IN ('department','agent')", name="ck_component_grant_grantee"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "component_key",
            "grantee_type",
            "grantee_id",
            name="uq_component_grant",
        ),
    )
    op.create_index("ix_component_grant_tenant_id", "component_grant", ["tenant_id"])
    op.create_index("ix_component_grant_component_key", "component_grant", ["component_key"])
    _rls("component_grant")


def downgrade() -> None:
    op.drop_table("component_grant")
