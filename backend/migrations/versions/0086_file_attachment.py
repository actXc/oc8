"""file_attachment table

Revision ID: 0086
Revises: 0085
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0086"
down_revision: str | None = "0085"
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
        "file_attachment",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("owner_type", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("bucket_key", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("is_image", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("bucket_key"),
        sa.CheckConstraint(
            "owner_type IN ('chat_message','agent_instructions')",
            name="ck_file_attachment_owner_type",
        ),
    )
    op.create_index("ix_file_attachment_owner_id", "file_attachment", ["owner_id"])
    op.create_index("ix_file_attachment_tenant_id", "file_attachment", ["tenant_id"])
    _rls("file_attachment")


def downgrade() -> None:
    op.drop_table("file_attachment")
