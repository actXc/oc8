"""budget table + task.state widened for budget_exceeded (§15.4)

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = dict(server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "budget",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=True),
        sa.Column("soft_limit_tokens", sa.BigInteger(), nullable=True),
        sa.Column("hard_limit_tokens", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.UniqueConstraint("tenant_id", "department_id", name="uq_budget_scope"),
    )
    op.create_index("ix_budget_tenant_id", "budget", ["tenant_id"])
    op.create_index("ix_budget_department_id", "budget", ["department_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_budget_tenant_wide ON budget (tenant_id) "
        "WHERE department_id IS NULL"
    )
    op.execute("ALTER TABLE budget ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON budget "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )

    op.execute("ALTER TABLE task DROP CONSTRAINT IF EXISTS ck_task_state")
    op.execute(
        "ALTER TABLE task ADD CONSTRAINT ck_task_state "
        "CHECK (state IN ('backlog','in_progress','waiting_for_approval','done',"
        "'failed','budget_exceeded'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE task DROP CONSTRAINT IF EXISTS ck_task_state")
    op.execute(
        "ALTER TABLE task ADD CONSTRAINT ck_task_state "
        "CHECK (state IN ('backlog','in_progress','waiting_for_approval','done','failed'))"
    )
    op.execute("DROP INDEX IF EXISTS uq_budget_tenant_wide")
    op.drop_table("budget")
