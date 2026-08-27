"""data_source gains last_sync_status/last_sync_error -- a failed sync used to
leave `connected` (transport-reachable, unrelated to sync outcome) as the
only status signal, so the UI stayed green after an error the user could
only catch in a toast that had already scrolled away.

Revision ID: 0072
Revises: 0071
Create Date: 2026-08-24
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # data_source is in migration 0001's create_all frozen set (cf. 0045,
    # 0070): a database built fresh from the LIVE ORM models already has these
    # columns and the constraint by the time this migration runs. IF NOT
    # EXISTS, and DROP-then-ADD for the constraint, converge the fresh-install
    # path with the incremental-deploy path.
    op.execute("ALTER TABLE data_source ADD COLUMN IF NOT EXISTS last_sync_status text")
    op.execute("ALTER TABLE data_source ADD COLUMN IF NOT EXISTS last_sync_error text")
    op.execute("ALTER TABLE data_source DROP CONSTRAINT IF EXISTS ck_data_source_last_sync_status")
    op.execute(
        "ALTER TABLE data_source ADD CONSTRAINT ck_data_source_last_sync_status "
        "CHECK (last_sync_status IS NULL OR last_sync_status = ANY (ARRAY['ok','failed']))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE data_source DROP CONSTRAINT IF EXISTS ck_data_source_last_sync_status")
    op.execute("ALTER TABLE data_source DROP COLUMN IF EXISTS last_sync_error")
    op.execute("ALTER TABLE data_source DROP COLUMN IF EXISTS last_sync_status")
