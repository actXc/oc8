"""dashboard_preset table -- a member's saved "My Work" arrangement, personal
or tenant-wide.

Revision ID: 0089
Revises: 0088
Create Date: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0089"
down_revision: str | None = "0088"
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
        "dashboard_preset",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("widgets", JSONB(), nullable=False, server_default="[]"),
        sa.Column("scope", sa.Text(), nullable=False, server_default="personal"),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.CheckConstraint("scope IN ('personal','tenant')", name="ck_dashboard_preset_scope"),
    )
    op.create_index("ix_dashboard_preset_tenant_id", "dashboard_preset", ["tenant_id"])
    _rls("dashboard_preset")


def downgrade() -> None:
    op.drop_table("dashboard_preset")
