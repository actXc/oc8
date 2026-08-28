"""channel_poll_cursor: where a poll-based approval channel (one with no
public webhook URL, e.g. telegram_approvals running with no tunnel) left
off reading the platform's own update queue. One row per (tenant, channel).

Revision ID: 0079
Revises: 0078
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0079"
down_revision: str | None = "0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = dict(server_default=sa.func.now(), nullable=False)


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def upgrade() -> None:
    op.create_table(
        "channel_poll_cursor",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("last_update_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.UniqueConstraint("tenant_id", "channel", name="uq_channel_poll_cursor_tenant_channel"),
    )
    op.create_index(
        "ix_channel_poll_cursor_tenant_id", "channel_poll_cursor", ["tenant_id"]
    )
    _rls("channel_poll_cursor")


def downgrade() -> None:
    op.drop_table("channel_poll_cursor")
