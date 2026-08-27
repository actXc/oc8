"""oauth_connection.grant_type + azure_tenant_id (app-only Graph/service-account auth).

Revision ID: 0057
Revises: 0056
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE oauth_connection "
        "ADD COLUMN grant_type text NOT NULL DEFAULT 'authorization_code'"
    )
    op.execute("ALTER TABLE oauth_connection ADD COLUMN azure_tenant_id text")
    op.execute(
        "ALTER TABLE oauth_connection ADD CONSTRAINT ck_oauth_conn_grant_type "
        "CHECK (grant_type IN ('authorization_code','client_credentials','service_account'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE oauth_connection DROP CONSTRAINT ck_oauth_conn_grant_type")
    op.execute("ALTER TABLE oauth_connection DROP COLUMN azure_tenant_id")
    op.execute("ALTER TABLE oauth_connection DROP COLUMN grant_type")
