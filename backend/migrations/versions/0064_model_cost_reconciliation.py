"""model_cost_reconciliation -- tenant-scoped, RLS. Anthropic/OpenAI-reported
cost vs. oc8's own calculation, daily granularity, on-demand fetch only
(Cost Center design, 2026-08-19 spec Part C).

Revision ID: 0064
Revises: 0063
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0064"
down_revision: str | None = "0063"
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
        CREATE TABLE model_cost_reconciliation (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL,
          provider text NOT NULL,
          report_date date NOT NULL,
          oc8_calculated_cost_micros bigint NOT NULL,
          provider_reported_cost_micros bigint,
          fetched_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_model_cost_reconciliation UNIQUE (tenant_id, provider, report_date)
        )
    """)
    op.execute(
        "CREATE INDEX ix_model_cost_reconciliation_tenant_id "
        "ON model_cost_reconciliation (tenant_id)"
    )
    _rls("model_cost_reconciliation")


def downgrade() -> None:
    op.drop_table("model_cost_reconciliation")
