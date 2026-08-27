"""audit_chain_checkpoint.keyed_count: store the signed keyed-row count (§12.5)

0031 signed a keyed-row count that was DERIVED LIVE at both signing and
verification. That count is stable against appends but not against deletions --
and a deletion below the mark is a truncation, the one break the system is
designed to recover from.

The consequence was that a keyed deployment could never recover from one. The
truncation branch skips the state block, so the break-path re-sign (which must
happen: the message binds the resumption pair, and that pair retreats on a
break) baked the SHORT live count into the marker while rows were missing.
Restoring the rows -- the documented remedy -- then made the marker fail
authentication, reporting mac_downgrade with may_sign cleared, which is
deliberately unclearable in band. The tenant was bricked, and the operator was
told they had been attacked.

Storing the count fixes it without weakening anything: the column is inside the
MAC message, so an attacker can lower it but cannot re-sign it, and lowering it
without the key fails authentication. It is advanced only by a clean pass;
break paths re-sign with the value already stored.

Backfill: the live derivation, which is exactly what the existing markers were
signed over, so every marker written by 0031 stays valid. (A tenant that is
mid-truncation at upgrade time backfills short, which is the same position it
was already in -- and the count is compared with `<`, so the restore still
clears it.)

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_chain_checkpoint",
        sa.Column("keyed_count", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.execute(
        """
        UPDATE audit_chain_checkpoint c
           SET keyed_count = (SELECT count(*) FROM audit_event e
                               WHERE e.tenant_id = c.tenant_id
                                 AND e.seq <= c.max_seen_seq
                                 AND e.mac_version >= c.max_mac_version)
        """
    )


def downgrade() -> None:
    # Dropping the column is enough: 0031-era code re-derives the count live,
    # which is what the backfill above put here, so markers stay valid.
    op.drop_column("audit_chain_checkpoint", "keyed_count")
