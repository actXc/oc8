"""Secret-blind Copilot proposal and operation records.

Revision ID: 0053
Revises: 0052
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def upgrade() -> None:
    op.execute("""
        CREATE TABLE copilot_proposal (
          id uuid PRIMARY KEY, tenant_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(), status text NOT NULL DEFAULT 'draft',
          created_by text NOT NULL, applied_by text, revision integer NOT NULL DEFAULT 1,
          idempotency_key uuid NOT NULL,
          CONSTRAINT ck_copilot_proposal_status
            CHECK (status IN ('draft','applied','expired','rejected')),
          CONSTRAINT uq_copilot_proposal_idempotency UNIQUE (tenant_id, idempotency_key)
        )
    """)
    op.execute("CREATE INDEX ix_copilot_proposal_tenant_id ON copilot_proposal (tenant_id)")
    op.execute("""
        CREATE TABLE copilot_operation (
          id uuid PRIMARY KEY, tenant_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          proposal_id uuid NOT NULL, ordinal integer NOT NULL,
          operation_type text NOT NULL, configuration jsonb NOT NULL, target_revision text,
          CONSTRAINT uq_copilot_operation_order UNIQUE (proposal_id, ordinal)
        )
    """)
    op.execute("CREATE INDEX ix_copilot_operation_tenant_id ON copilot_operation (tenant_id)")
    op.execute("CREATE INDEX ix_copilot_operation_proposal_id ON copilot_operation (proposal_id)")
    _rls("copilot_proposal")
    _rls("copilot_operation")


def downgrade() -> None:
    op.drop_table("copilot_operation")
    op.drop_table("copilot_proposal")
