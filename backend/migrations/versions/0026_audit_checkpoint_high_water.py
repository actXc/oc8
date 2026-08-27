"""audit_chain_checkpoint high-water mark + first-break residue (§12.5)

max_seen_seq is monotonic: the truncation check evaluates against it instead of
last_seq, which retreats to the surviving tail on a break and would otherwise
consume the only evidence that rows went missing.

first_break_at is written once and never cleared, so a full re-verification can
return status to "ok" without erasing the fact that a break was ever observed.

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_chain_checkpoint",
        sa.Column("max_seen_seq", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "audit_chain_checkpoint",
        sa.Column("first_break_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Existing checkpoints: seed the high-water mark from what they already
    # proved verified, so a truncation after this migration is still caught.
    op.execute("UPDATE audit_chain_checkpoint SET max_seen_seq = last_seq")
    # A checkpoint already sitting in "broken" carries a break that predates
    # this column; stamp it so the residue is not silently lost.
    op.execute(
        "UPDATE audit_chain_checkpoint SET first_break_at = verified_at WHERE status = 'broken'"
    )


def downgrade() -> None:
    op.drop_column("audit_chain_checkpoint", "first_break_at")
    op.drop_column("audit_chain_checkpoint", "max_seen_seq")
