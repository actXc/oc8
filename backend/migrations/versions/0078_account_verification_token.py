"""account_verification_token: single-use, time-limited, mailed tokens
backing self-service password-reset and email-change confirmation (design:
docs/superpowers/specs/2026-08-28-account-self-service-design.md §4.4).

Revision ID: 0078
Revises: 0077
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0078"
down_revision: str | None = "0077"
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
        "account_verification_token",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("new_email", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.CheckConstraint(
            "purpose IN ('password_reset','email_change')",
            name="ck_account_verification_token_purpose",
        ),
    )
    op.create_index(
        "ix_account_verification_token_tenant_id", "account_verification_token", ["tenant_id"]
    )
    op.create_index(
        "ix_account_verification_token_member_id", "account_verification_token", ["member_id"]
    )
    op.create_index(
        "ix_account_verification_token_token_hash",
        "account_verification_token",
        ["token_hash"],
        postgresql_where=sa.text("used_at IS NULL"),
    )
    _rls("account_verification_token")


def downgrade() -> None:
    op.drop_table("account_verification_token")
