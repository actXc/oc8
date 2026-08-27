"""record_claim — two agents must not work the same record at once

A department with two agents polling one queue will hand both of them the same
oldest item: they read it in the same second, and the mission rule "claim the
ticket before you write" cannot close a race it does not know about. The
customer then gets two answers, or worse, two different ones.

The claim is on the RECORD, not on the ticket system: the core learns which
record a call touches from the connection's own focus_spec, so this holds for
any software a plugin connects. One row per (entity, record) is what makes the
uniqueness enforceable at all -- the database decides the race, not the agents.

Revision ID: 0039
Revises: 0038
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IF NOT EXISTS throughout: test databases are built from the live ORM
    # models, so this migration runs against a schema that already has the table.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS record_claim (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            entity text NOT NULL,
            record_ref text NOT NULL,
            run_id uuid NOT NULL,
            agent_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_record_claim UNIQUE (tenant_id, entity, record_ref)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_record_claim_tenant_id ON record_claim (tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_record_claim_run_id ON record_claim (run_id)")
    op.execute("ALTER TABLE record_claim ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON record_claim")
    op.execute(
        "CREATE POLICY tenant_isolation ON record_claim "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS record_claim")
