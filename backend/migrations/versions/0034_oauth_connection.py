"""oauth_connection table with RLS; data_source.oauth_connection_id (§11.2)

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = dict(server_default=sa.func.now(), nullable=False)


def _rls(table: str) -> None:
    # The blanket policy in 0001 covers only the tables that existed then --
    # every new tenant-scoped table must restate it (cf. 0023_secret.py).
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def upgrade() -> None:
    op.create_table(
        "oauth_connection",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("account_label", sa.Text(), nullable=False),
        sa.Column("scopes", ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("access_secret_ref", sa.Text(), nullable=False),
        sa.Column("refresh_secret_ref", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("client_source", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.UniqueConstraint(
            "tenant_id", "provider", "account_label", name="uq_oauth_conn_tenant_provider_account"
        ),
        sa.CheckConstraint(
            "status IN ('active','needs_reauth','revoked')", name="ck_oauth_conn_status"
        ),
        sa.CheckConstraint(
            "client_source IN ('tenant','platform')", name="ck_oauth_conn_client_source"
        ),
    )
    op.create_index("ix_oauth_connection_tenant_id", "oauth_connection", ["tenant_id"])
    _rls("oauth_connection")

    # data_source is in migration 0001's create_all frozen set (cf. 0021, 0022):
    # the model declares oauth_connection_id now, so a fresh DB already has the
    # column by the time this migration runs. IF NOT EXISTS/IF EXISTS converges
    # the fresh-install path with the incremental-migration path.
    op.execute("ALTER TABLE data_source ADD COLUMN IF NOT EXISTS oauth_connection_id uuid")
    op.create_foreign_key(
        "fk_data_source_oauth_connection",
        "data_source",
        "oauth_connection",
        ["oauth_connection_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Dead pointer: wired to nothing, read nowhere in src or tests. Removed
    # rather than left to compete with oauth_connection_id. A fresh DB never had
    # it (the model no longer declares it), hence IF EXISTS.
    op.execute("ALTER TABLE data_source DROP COLUMN IF EXISTS credentials_ref")


def downgrade() -> None:
    op.execute("ALTER TABLE data_source ADD COLUMN IF NOT EXISTS credentials_ref text")
    op.drop_constraint("fk_data_source_oauth_connection", "data_source", type_="foreignkey")
    op.execute("ALTER TABLE data_source DROP COLUMN IF EXISTS oauth_connection_id")
    op.drop_table("oauth_connection")
