"""Add used_by_copilot flag to model_config.

Single-select: at most one ModelConfig per tenant is flagged as the one Copilot
uses. Enforced in the create/update service (catalog.py), not by a DB
constraint -- see backend/src/oc8/models/core.py's ModelConfig docstring.

`model_config` is one of the tables 0001_initial.py creates via a live
`Base.metadata.create_all()` (see that file's `_TABLES_AT_0001`/`_snapshot_tables`),
so on a fresh database this column already exists by the time 0001 finishes --
a plain `add_column` here would raise DuplicateColumn on migrate-from-scratch
(e.g. the test suite's throwaway Postgres). `IF NOT EXISTS` / `IF EXISTS` guards
both directions, matching the same hazard's workaround in 0054 (`agent.config_revision`).

Revision ID: 0055
Revises: 0054
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE model_config ADD COLUMN IF NOT EXISTS "
        "used_by_copilot boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE model_config DROP COLUMN IF EXISTS used_by_copilot")
