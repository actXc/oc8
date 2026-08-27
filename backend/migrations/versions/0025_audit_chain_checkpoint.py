"""audit_chain_checkpoint table with RLS (§12.5 tamper detection)

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def upgrade() -> None:
    op.create_table(
        "audit_chain_checkpoint",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_hash", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="ok"),
        sa.Column("broken_at_seq", sa.BigInteger(), nullable=True),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", name="uq_audit_checkpoint_tenant"),
    )
    op.create_index("ix_audit_chain_checkpoint_tenant_id", "audit_chain_checkpoint", ["tenant_id"])
    _rls("audit_chain_checkpoint")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON audit_chain_checkpoint")
    op.drop_index("ix_audit_chain_checkpoint_tenant_id", table_name="audit_chain_checkpoint")
    op.drop_table("audit_chain_checkpoint")
