"""Web Push subscriptions per operator device.

Revision ID: 0057
Revises: 0056
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
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
        CREATE TABLE push_subscription (
          id uuid PRIMARY KEY, tenant_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          member_id uuid NOT NULL REFERENCES org_member(id) ON DELETE CASCADE,
          endpoint text NOT NULL,
          p256dh text NOT NULL,
          auth text NOT NULL,
          user_agent text,
          -- Scoped per tenant, not global: the push service assigns one
          -- endpoint per browser installation against this instance's single
          -- VAPID key pair, so the same browser presents the same endpoint
          -- under every tenant its operator belongs to. A global UNIQUE would
          -- turn the second tenant's subscribe into an IntegrityError that no
          -- RLS-scoped SELECT could have detected first.
          CONSTRAINT uq_push_subscription_endpoint UNIQUE (tenant_id, endpoint)
        )
    """)
    op.execute("CREATE INDEX ix_push_subscription_tenant_id ON push_subscription (tenant_id)")
    op.execute("CREATE INDEX ix_push_subscription_member_id ON push_subscription (member_id)")
    _rls("push_subscription")


def downgrade() -> None:
    op.drop_table("push_subscription")
