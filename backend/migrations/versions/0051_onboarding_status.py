"""onboarding_status on organization, so a first-time admin can be routed to
the gamified onboarding wizard instead of an empty dashboard

Every tenant starts 'pending'. Existing tenants that already have at least one
agent have already passed the point this wizard exists to cover, so they're
backfilled straight to 'completed' -- nobody who's already set up gets an
unexpected wizard on next login.

NOTE: organization is in migration 0001's create_all frozen set, so the model
declares this column and a fresh DB already has it -- IF NOT EXISTS converges
the migrated-DB path with the fresh-DB path for the column itself. The model
uses Python-side `default=`, not `server_default=`, so a fresh DB's column has
no DB-level default; the unconditional `SET DEFAULT` below converges that too
(a no-op on an already-migrated DB, where the default is already set).

Revision ID: 0051
Revises: 0050
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE organization ADD COLUMN IF NOT EXISTS onboarding_status text "
        "NOT NULL DEFAULT 'pending'"
    )
    op.execute("ALTER TABLE organization ALTER COLUMN onboarding_status SET DEFAULT 'pending'")
    op.execute(
        "ALTER TABLE organization DROP CONSTRAINT IF EXISTS ck_organization_onboarding_status"
    )
    op.execute(
        "ALTER TABLE organization ADD CONSTRAINT ck_organization_onboarding_status "
        "CHECK (onboarding_status IN ('pending','completed','skipped'))"
    )
    op.execute(
        """
        UPDATE organization SET onboarding_status = 'completed'
        WHERE id IN (SELECT DISTINCT tenant_id FROM agent)
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_organization_onboarding_status", "organization", type_="check")
    op.drop_column("organization", "onboarding_status")
