"""revoke DELETE on audit_chain_checkpoint from oc8_app (§12.5)

Deleting the checkpoint row resets tamper evidence: _ensure_checkpoint (see
oc8.audit.integrity) rebuilds a fresh, clean checkpoint over whatever survives
the moment it next runs -- silently erasing both an in-progress truncation and
the write-once first_break_at residue. That primitive is currently available
to the same application role a database-level attacker would already need to
bypass to tamper with audit_event itself, which is a lower bar than the deal
migration 0001 struck for audit_event: DELETE requires the owner role, not the
runtime one.

The verification job (oc8.audit.integrity._run) only ever INSERTs a fresh
checkpoint or UPDATEs an existing one -- it never deletes a row -- so revoking
DELETE costs the running application nothing. UPDATE stays granted: unlike
audit_event, this table is legitimately mutable and is rewritten on every
verification pass. Follows the REVOKE style migration 0001 uses for
_IMMUTABLE_TABLES, but only revokes DELETE, not UPDATE.

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "audit_chain_checkpoint"


def upgrade() -> None:
    op.execute(f"REVOKE DELETE ON {_TABLE} FROM oc8_app")


def downgrade() -> None:
    op.execute(f"GRANT DELETE ON {_TABLE} TO oc8_app")
