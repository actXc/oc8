"""a lever a department head can be handed without handing her the whole tenant

Today the only way to start, pause, renarrow or reconfigure an agent is
tenant-wide `agent:manage` -- which also hands its holder every OTHER
department's agents, whether she asked for them or not. The seat mechanism
0046 built gives her a queue of approvals to work but no lever over the
workers in it. This migration is the whole of the lever: one boolean, on the
seat row she already holds, that never becomes a permission string.

**One column, not a third `seat_role`.** `agent_manage` rides ORTHOGONAL to
`ck_org_member_department_seat_role` -- a `dept_viewer` may hold it, a
`dept_approver` may not, and the CHECK on `seat_role` is untouched by this
migration. Folding it into `seat_role` would force a fourth enum value for
every combination and make "viewer who can also manage agents" unrepresentable
without adding a fifth.

**Never a `perm()` string, and that is the point, not an oversight.** This
column is read by two functions in `api/deps.py` (`require_agent_write`,
`authorize_agent_write`) that take no permission argument -- there is no
`perm()` call anywhere in the write path for a tenant-defined role (0047) to
target. A tenant administrator composing a custom role can build one that
holds `agent:view` tenant-wide; she can never build one that holds this. The
only writer of the column is `grant_seat`, itself reachable only through the
`member:manage`-gated route -- so the toggle cannot be self-granted, granted
by a peer, or granted by a role.

**`NOT NULL DEFAULT false`, fail-closed on rollout.** Every seat that exists
the instant this migration runs gets zero write authority over agents. Read
authority is untouched by this migration entirely -- it graduates in
`authz/permissions.py`, a code change, not a schema one; a live seat could
theoretically see agents in its department before this migration and after,
with no column here bearing on that.

**No new index.** The existing partial unique index `uq_org_member_department_
live` on `(member_id, department_id) WHERE revoked_at IS NULL` already makes
"does this member hold a live seat here, and what does it grant" a single-row,
index-covered lookup; `agent_manage` rides as one more SELECTed column on that
same row.

Revision ID: 0048
Revises: 0047
Create Date: 2026-08-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IF NOT EXISTS: the documented hazard in this repo (0010, 0021, 0037,
    # 0045, 0046, 0047) -- a test database is built by 0001's `create_all` over
    # the LIVE ORM model, so on a fresh install this column already exists by
    # the time this runs, while a real deployment does not.
    op.execute(
        "ALTER TABLE org_member_department "
        "ADD COLUMN IF NOT EXISTS agent_manage boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    """Lossy, and said out loud: every department head an admin promoted with
    this toggle loses the ability to start, pause, renarrow or reconfigure her
    own team's agents the instant this runs -- no audit event marks it, because
    the column that carried the grant is gone. Re-applying 0048 afterwards
    restores an empty column, not the grants that were on it; those are only
    recoverable from `member.seat_granted` audit history, not from the schema.
    """
    op.execute("ALTER TABLE org_member_department DROP COLUMN IF EXISTS agent_manage")
