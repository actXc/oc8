"""agent_run remembers what became of its evidence

A run's session folder is what answers WHY an agent did something (§12.5.1), and
until now nothing archived, chained or pruned it. The sweep that does needs an
exact work list: scanning every terminal run and stat()ing the filesystem to
find out which ones still have a folder is a full scan of all history on every
tick, and it grows for ever.

So the row says so itself. 'present' is a claim about the filesystem, which is
why existing rows get it: every run that ever executed has a folder until the
sweep takes it, and a run that turns out to have none is corrected to 'none' the
first time the sweep looks. Being wrong in that direction costs one stat; being
wrong the other way would leave evidence on disk that nothing ever comes back
for.

Revision ID: 0043
Revises: 0042
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATES = "'present','archived','reduced','none'"


def upgrade() -> None:
    op.add_column(
        "agent_run",
        sa.Column("evidence_state", sa.Text(), nullable=False, server_default="present"),
    )
    op.add_column(
        "agent_run",
        sa.Column("evidence_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_agent_run_evidence_state",
        "agent_run",
        f"evidence_state = ANY (ARRAY[{_STATES}])",
    )
    # The sweep's whole query is (tenant, evidence_state) -- partial, because
    # 'archived' and 'reduced' rows accumulate for ever and are never the ones
    # being looked for.
    op.execute(
        "CREATE INDEX ix_agent_run_evidence_pending ON agent_run (tenant_id, updated_at) "
        "WHERE evidence_state = 'present'"
    )
    op.execute(
        "CREATE INDEX ix_agent_run_evidence_archived ON agent_run (tenant_id, evidence_at) "
        "WHERE evidence_state = 'archived'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_run_evidence_archived")
    op.execute("DROP INDEX IF EXISTS ix_agent_run_evidence_pending")
    op.drop_constraint("ck_agent_run_evidence_state", "agent_run", type_="check")
    op.drop_column("agent_run", "evidence_at")
    op.drop_column("agent_run", "evidence_state")
