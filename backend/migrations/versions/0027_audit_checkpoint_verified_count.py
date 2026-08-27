"""audit_chain_checkpoint.verified_count + break_kind (§12.5)

The high-water MARK added in 0026 is not a survivable invariant. seq is a
global identity column, so after rows are deleted the tenant's tail climbs
back over the mark as soon as any new event is appended -- and on a live
tenant every approval decision and tool action appends. The truncation check
then goes quiet and an operator-invoked full verification turns the banner
green over rows that are still missing.

verified_count fixes the invariant: it stores how many rows existed at or
below max_seen_seq at the last clean pass. Later appends carry a seq ABOVE the
mark, so they cannot inflate that count -- it stays short until the missing
rows are actually restored. It also catches deletions strictly inside an
already-checkpointed range, which incremental verification structurally
cannot see.

break_kind distinguishes 'hash_mismatch' (full re-verification is the remedy
path after a legitimate restore) from 'truncation' (nothing but the rows
coming back can clear it), so the operator screen can stop recommending an
action that cannot help.

Seeding: verified_count is COUNT(seq <= max_seen_seq) computed now, seeded
UNCONDITIONALLY -- there is no separate case for a pre-existing 'truncation'.

An earlier draft of this migration tried to key a "this pre-existing break is
a truncation" signature off `last_seq < max_seen_seq` and seed those rows
COUNT(...) + 1 (permanently unsatisfiable, matching a real truncation's
semantics). That signature does not discriminate: the 0026-era integrity.py
advanced max_seen_seq unconditionally on every pass -- `cp.max_seen_seq =
max(high_water, tail_seq, last_seq)` sat outside the `if broken_at is not
None` branch -- not only on a clean one. So a HASH-MISMATCH break also left
last_seq < max_seen_seq the moment a later event was appended: last_seq stays
pinned to before the break while max_seen_seq keeps climbing with the tail. A
hash-mismatch checkpoint would have been mislabelled 'truncation' and seeded
one-higher-than-live -- stranded forever, unsatisfiable, with nothing actually
missing.

That mislabelling can never happen here in practice: 0026 and 0027 ship in the
same release, so no database was ever verified by 0026-era application code
(the only code whose unconditional max_seen_seq advance could produce that
false signature) while this column existed to seed from. Every checkpoint
this migration finds already 'broken' predates both max_seen_seq's existence
and this feature's truncation detection entirely, so it can only be a genuine
hash mismatch. Every pre-existing broken row is labelled hash_mismatch, and
verified_count is seeded the same way for every row, broken or not.

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LIVE_COUNT = """
    (SELECT count(*) FROM audit_event e
      WHERE e.tenant_id = audit_chain_checkpoint.tenant_id
        AND e.seq <= audit_chain_checkpoint.max_seen_seq)
"""

# No pre-existing checkpoint can be a truncation (see the module docstring),
# so every pre-existing break gets the same label.
LABEL_SQL = "UPDATE audit_chain_checkpoint SET break_kind = 'hash_mismatch' WHERE status = 'broken'"
SEED_SQL = f"UPDATE audit_chain_checkpoint SET verified_count = {_LIVE_COUNT}"


def upgrade() -> None:
    op.add_column(
        "audit_chain_checkpoint",
        sa.Column("verified_count", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("audit_chain_checkpoint", sa.Column("break_kind", sa.Text(), nullable=True))
    op.execute(LABEL_SQL)
    op.execute(SEED_SQL)


def downgrade() -> None:
    op.drop_column("audit_chain_checkpoint", "break_kind")
    op.drop_column("audit_chain_checkpoint", "verified_count")
