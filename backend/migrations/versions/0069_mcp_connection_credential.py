"""mcp_connection gains credential_id; drop dead credentials_ref

Revision ID: 0069
Revises: 0068
Create Date: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # mcp_connection is in migration 0001's create_all frozen set (cf. 0034):
    # the model declares credential_id now, so a fresh DB already has the
    # column (and lacks credentials_ref) by the time this migration runs.
    # IF NOT EXISTS/IF EXISTS converges the fresh-install path with the
    # incremental-migration path.
    op.execute("ALTER TABLE mcp_connection ADD COLUMN IF NOT EXISTS credential_id uuid")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_mcp_connection_credential_id "
        "ON mcp_connection (credential_id)"
    )
    op.execute("ALTER TABLE mcp_connection DROP COLUMN IF EXISTS credentials_ref")


def downgrade() -> None:
    op.execute("ALTER TABLE mcp_connection ADD COLUMN IF NOT EXISTS credentials_ref text")
    op.execute("DROP INDEX IF EXISTS ix_mcp_connection_credential_id")
    op.execute("ALTER TABLE mcp_connection DROP COLUMN IF EXISTS credential_id")
