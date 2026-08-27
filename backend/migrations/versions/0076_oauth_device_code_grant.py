"""oauth_connection gains 'device_code' as a grant_type value, and a new
provider_metadata JSONB column for small non-secret per-connection facts a
provider's own flow needs to remember (e.g. the ChatGPT subscription auth
device-code flow's chatgpt_account_id, extracted from an id_token claim --
see oc8/oauth/tokens.py's extract_chatgpt_account_id, Task 4). Generic and
provider-agnostic by design, not named after ChatGPT specifically, so a
future provider with the same small-metadata need reuses this column
instead of inventing its own.

Revision ID: 0076
Revises: 0075
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0076"
down_revision: str | None = "0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE oauth_connection DROP CONSTRAINT ck_oauth_conn_grant_type")
    op.execute(
        "ALTER TABLE oauth_connection ADD CONSTRAINT ck_oauth_conn_grant_type "
        "CHECK (grant_type IN "
        "('authorization_code','client_credentials','service_account','device_code'))"
    )
    op.add_column(
        "oauth_connection",
        sa.Column(
            "provider_metadata",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("oauth_connection", "provider_metadata")
    op.execute(
        "UPDATE oauth_connection SET grant_type = 'authorization_code' "
        "WHERE grant_type = 'device_code'"
    )
    op.execute("ALTER TABLE oauth_connection DROP CONSTRAINT ck_oauth_conn_grant_type")
    op.execute(
        "ALTER TABLE oauth_connection ADD CONSTRAINT ck_oauth_conn_grant_type "
        "CHECK (grant_type IN "
        "('authorization_code','client_credentials','service_account'))"
    )
