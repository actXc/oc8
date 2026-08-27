"""totp_credential table + org_member/organization TOTP columns
(standalone 2FA design)

Revision ID: 0071
Revises: 0070
Create Date: 2026-08-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0071"
down_revision: str | None = "0070"
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
        "totp_credential",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("secret_ref", sa.Text(), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backup_codes", JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.UniqueConstraint("member_id", name="uq_totp_credential_member"),
    )
    op.create_index("ix_totp_credential_tenant_id", "totp_credential", ["tenant_id"])
    _rls("totp_credential")

    # organization and org_member are both in migration 0001's frozen
    # create_all set: the model declares these columns now, so a fresh DB
    # already has them by the time this migration runs. IF NOT EXISTS
    # converges the fresh-install path with the incremental-migration path,
    # same pattern as 0069's credential_id and 0070's
    # narrowing_overridden_keys columns.
    op.execute(
        "ALTER TABLE org_member ADD COLUMN IF NOT EXISTS totp_grace_started_at "
        "timestamptz NULL"
    )
    op.execute(
        "ALTER TABLE organization ADD COLUMN IF NOT EXISTS totp_step_up_enabled "
        "boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE organization DROP COLUMN IF EXISTS totp_step_up_enabled")
    op.execute("ALTER TABLE org_member DROP COLUMN IF EXISTS totp_grace_started_at")
    op.drop_table("totp_credential")
