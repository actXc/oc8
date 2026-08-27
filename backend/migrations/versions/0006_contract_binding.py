"""contract_binding table with RLS

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = dict(server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "contract_binding",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("handoff_type_id", sa.Uuid(), nullable=False),
        sa.Column("source_department_id", sa.Uuid(), nullable=False),
        sa.Column("target_department_id", sa.Uuid(), nullable=False),
        sa.Column("payload_map", JSONB(), nullable=False, server_default="{}"),
        sa.Column("gate", sa.Text(), nullable=False, server_default="auto"),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.CheckConstraint("gate IN ('auto','approval')", name="ck_contract_binding_gate"),
    )
    op.create_index("ix_contract_binding_tenant_id", "contract_binding", ["tenant_id"])
    op.create_index("ix_contract_binding_event_type", "contract_binding", ["event_type"])
    op.create_index(
        "ix_contract_binding_source_department_id", "contract_binding", ["source_department_id"]
    )
    op.execute("ALTER TABLE contract_binding ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON contract_binding "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.drop_table("contract_binding")
