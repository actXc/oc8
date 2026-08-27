"""memory_record.status + memory_store uniqueness

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotent: 0001_initial's `Base.metadata.create_all()` reflects LIVE model
    # definitions restricted to a frozen TABLE-name set, not a frozen column set.
    # A fresh DB bootstrapping from 0001 already gets `status` and the unique
    # constraint (since MemoryRecord/MemoryStore's model classes now declare
    # them); an existing DB migrated through 0009 needs this explicit ALTER.
    # `IF NOT EXISTS` / the DO-block guards make both paths converge safely.
    op.execute(
        "ALTER TABLE memory_record ADD COLUMN IF NOT EXISTS status text "
        "NOT NULL DEFAULT 'approved'"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_memory_record_status'
            ) THEN
                ALTER TABLE memory_record
                ADD CONSTRAINT ck_memory_record_status
                CHECK (status IN ('approved','pending','rejected'));
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_memory_store_tenant_tier_owner'
            ) THEN
                ALTER TABLE memory_store
                ADD CONSTRAINT uq_memory_store_tenant_tier_owner
                UNIQUE (tenant_id, tier, owner_id);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE memory_store DROP CONSTRAINT IF EXISTS uq_memory_store_tenant_tier_owner"
    )
    op.execute("ALTER TABLE memory_record DROP CONSTRAINT IF EXISTS ck_memory_record_status")
    op.execute("ALTER TABLE memory_record DROP COLUMN IF EXISTS status")
