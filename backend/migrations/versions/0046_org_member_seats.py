"""a person, the seats they hold, and the department an approval belongs to

There is no row for a human anywhere in this schema. A caller is five JWT claims,
so "his department, and not the whole company's" has no term it can be true in:
`GET /approvals` filters on status alone and returns every pending approval in
the tenant, and `POST /approvals/{id}/decision` checks a permission and never
asks whose approval it is. These two tables and one column are that missing term.

**`org_member` is WHO, `org_member_department` is a SEAT.** Splitting them is
what lets one person stand in two departments with different authority in each,
and what makes "revoke Anna's Vertrieb seat" one UPDATE that leaves her identity,
her channel binding and her decision history intact.

**Nothing here goes into the token.** Seats are read live from these rows, so a
revocation takes effect on the caller's next request rather than on their next
token refresh. For authority that releases money that is not a preference.

**Revoked, not deleted, throughout.** `revoked_at` and the partial unique index
over it exist because "who could approve this, and until when" is the first
question an audit asks, and a DELETE answers it with silence. The same reason
`approval_channel_binding.revoked_at` exists.

**No second RLS GUC**, and this migration is where that decision is visible by
its absence. A `app.department_scope` setting with a RESTRICTIVE policy was the
obvious mechanism and is rejected on measured evidence:
`tests/db/test_organization_rls_pooled.py:26-38` documents that once a physical
pooled connection has bound a custom GUC with `is_local=true`, its reset value is
`''` and never NULL for that connection's life -- migration 0015 exists for
exactly this -- so "unset means the system session" is a lie in production, and
the executor, the scheduler and `metering/budget.py` would intermittently see
zero `approval_request` rows and be unable to INSERT one. It also fails OPEN on
forget, which is the inverse of the property that makes `app.tenant_id`
trustworthy. The department term is therefore enforced in application code, in
one funnel and one repository, and `approval_request` gains a column but no new
policy -- so no `WITH CHECK` can turn a scope question into a raw 42501 for the
agent runtime raising an approval.

`department_id` is denormalised onto `approval_request` rather than joined
through `agent`: moving an agent to another department must not drag its
already-pending approvals into a queue whose people were never asked.

Revision ID: 0046
Revises: 0045
Create Date: 2026-08-01
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

#: The same two words as `authz.permissions.SEAT_PERMISSIONS`, and
#: `tests/authz/test_seat_vocabulary.py` fails if the two ever disagree. A seat
#: naming a built-in role -- `dept_manager`, which is nine tenant-wide `:manage`
#: grants -- is unrepresentable in the table rather than merely discouraged.
_SEAT_ROLES = "'dept_viewer','dept_approver'"

#: The agent is authoritative, so every historical row lands somewhere:
#: `approval_request.agent_id` is NOT NULL, `agent.department_id` is NOT NULL,
#: and `agent` is soft-deleted rather than removed. Imperfect only for agents
#: that have already been moved between departments -- their old approvals get
#: today's department, and that is the only evidence that exists.
#:
#: `ag.tenant_id = a.tenant_id` is not decoration. Without it, a corrupted
#: `agent_id` pointing at another tenant's agent would silently file the approval
#: into a FOREIGN tenant's department id. With it, such a row stays NULL, which
#: means tenant-wide and is visible only to somebody unrestricted -- wrong in the
#: direction that shows a CEO one row too many, not in the direction that shows
#: another company's department a decision to make.
_BACKFILL = """
UPDATE approval_request a
   SET department_id = ag.department_id
  FROM agent ag
 WHERE ag.id = a.agent_id
   AND ag.tenant_id = a.tenant_id
   AND a.department_id IS NULL
"""


def _tenant_isolated(table: str) -> None:
    """RLS exactly as every other tenant table has it, copied rather than
    abstracted so `grep tenant_isolation` keeps finding all of them."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    # `docker/init-db.sql` sets ALTER DEFAULT PRIVILEGES for tables created BY
    # oc8_migrate, which covers a cluster bootstrapped from that file. Said out
    # loud anyway, because a database restored from a dump or bootstrapped
    # before that statement existed has no default privileges, and the symptom
    # is the runtime role getting `permission denied` on a table it can see in
    # the catalog. Guarded on the role existing so a single-role developer
    # database is not broken by a GRANT to a role it never created.
    op.execute(
        f"""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'oc8_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO oc8_app;
          END IF;
        END $$
        """
    )


def upgrade() -> None:
    # IF NOT EXISTS throughout, the documented hazard in this repo (0010, 0021,
    # 0037, 0045): a test database is built by 0001's `create_all` over the LIVE
    # ORM models for the frozen table set, so on a fresh install
    # `approval_request` ALREADY has `department_id` and its plain index by the
    # time this runs, while a real deployment does not. `org_member` is not in
    # that frozen set, so it is always created here.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS org_member (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            subject text NOT NULL,
            subject_uuid uuid NOT NULL,
            display_name text NOT NULL DEFAULT '',
            all_departments boolean NOT NULL DEFAULT false,
            deleted_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_member_tenant_id ON org_member (tenant_id)")
    # The HTTP door's lookup: the token carries the subject string.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_org_member_subject ON org_member "
        "(tenant_id, subject) WHERE deleted_at IS NULL"
    )
    # The messenger door's lookup: an inbound message carries no token at all,
    # only `approval_channel_binding.user_id`, which is this uuid. Both are
    # partial on `deleted_at IS NULL` so a removed member never blocks
    # re-enrolling the same person.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_org_member_subject_uuid ON org_member "
        "(tenant_id, subject_uuid) WHERE deleted_at IS NULL"
    )
    _tenant_isolated("org_member")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS org_member_department (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            member_id uuid NOT NULL,
            department_id uuid NOT NULL,
            seat_role text NOT NULL,
            granted_by uuid,
            revoked_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_org_member_department_seat_role
                CHECK (seat_role IN ({_SEAT_ROLES}))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_org_member_department_tenant_id "
        "ON org_member_department (tenant_id)"
    )
    # The resolver's read, once per request: every live seat this member holds.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_org_member_department_member_id "
        "ON org_member_department (member_id)"
    )
    # "who may decide in this department", which is what the messenger fan-out
    # asks before it sends anything.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_org_member_department_department_id "
        "ON org_member_department (department_id)"
    )
    # One LIVE seat per person per department. Partial rather than a plain
    # constraint, so re-granting a revoked seat is an INSERT and the history
    # survives; a full unique key would force the revoke path to DELETE.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_org_member_department_live "
        "ON org_member_department (member_id, department_id) WHERE revoked_at IS NULL"
    )
    _tenant_isolated("org_member_department")

    op.execute("ALTER TABLE approval_request ADD COLUMN IF NOT EXISTS department_id uuid")
    # The plain btree the ORM's `index=True` produces, created here so an
    # incremental deployment ends up with the same indexes as a fresh install
    # built by `create_all`. Without this line the two schemas diverge silently
    # and only under load.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_approval_request_department_id "
        "ON approval_request (department_id)"
    )
    # The workspace queue's read: pending approvals in the departments I hold a
    # seat in. Partial, because `pending` is a tiny and draining minority of the
    # table's lifetime rows -- the same shape 0043 and 0045 use.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_approval_request_pending_department "
        "ON approval_request (department_id) WHERE status = 'pending'"
    )

    result = op.get_bind().execute(sa.text(_BACKFILL))
    # Say the number out loud. If the migration role turns out to be subject to
    # RLS the UPDATE silently touches nothing -- fail-safe, but a 0 that means
    # "not permitted" must not be read as a 0 that means "nothing to do". This
    # matters more here than in 0045: a row left NULL is a row only the
    # unrestricted can see, so a silently skipped backfill looks exactly like a
    # working department filter.
    logger.info("0046: filed %s existing approvals under their agent's department", result.rowcount)

    # No backfill, and no grandfathering. A binding with member_id NULL decides
    # nothing from this slice on. Inserting a synthetic `org_member` per binding
    # (subject = 'channel:' || user_id) was the alternative and is rejected: it
    # collides with uq_org_member_subject_uuid the first time that same human
    # authenticates, which 500s every request from exactly the people piloting
    # the feature. Existing bindings are re-issued through
    # `POST /channels/{channel}/link`; on the live system that is one row.
    op.execute("ALTER TABLE approval_channel_binding ADD COLUMN IF NOT EXISTS member_id uuid")


def downgrade() -> None:
    """Drops two tables and two columns; nothing else is altered.

    Lossy, and said out loud: the seats go with the tables, so downgrading and
    upgrading again leaves every employee seatless -- an empty queue and a "you
    are not assigned to a department" screen, not an error. `department_id`'s
    backfill is re-derivable from the agent, so re-applying 0046 restores it.
    """
    op.execute("ALTER TABLE approval_channel_binding DROP COLUMN IF EXISTS member_id")
    # Both indexes on the column go with it.
    op.execute("ALTER TABLE approval_request DROP COLUMN IF EXISTS department_id")
    op.execute("DROP TABLE IF EXISTS org_member_department")
    op.execute("DROP TABLE IF EXISTS org_member")
