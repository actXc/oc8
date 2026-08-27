"""a role a tenant's own administrator wrote, and the person it was given to

Until now the ladder had five rungs and all five were in code: `org_admin` (52
of 52 permissions), `dept_manager` (29), `operator` (20), `auditor` (19), and
`member` (0). A tenant that wants somebody who may sign off offers and answer an
agent's questions and read nothing else had to choose between handing them
twenty permissions or none. Worse, the rung was not even assignable -- every
human on every live tenant is minted `org_admin` and nothing passes `role=` --
so the only lever that could narrow anybody was a free-text user attribute in an
identity provider oc8 cannot write to. This migration is the schema for the
lever: a NAME, a SET of permission strings, and one nullable column saying who
holds it.

**Zero rows are written. There is no step 9.** No seed, no backfill, no repair of
the tenant that has one role row where another has five. Both new surfaces start
empty on every tenant, new and existing alike, and empty produces exactly today's
behaviour -- `org_member.role_id IS NULL` means "the token decides", which is
what happens now. So there is nothing to migrate and nothing that can be
half-applied. (`UPDATE role SET kind = 'agent'` in step 3 is a re-labelling of
rows that already exist, not a grant: it moves no authority in either
direction.)

**`kind` is the fix for a live trap, and it is why this is a column and not a
naming convention.** `authz/pdp.py` resolves an agent's `role_id` back through
the CODE dict BY NAME, so on today's schema a tenant that creates a role called
`agent_default` and points an agent at it hands that agent read, write AND send.
After this migration the two resolvers ask different questions of the same
table: the agent path takes only `kind='agent'`, the human path only
`kind='human'`, and no amount of cleverness with a name reaches across.

**The two CHECKs on `permission` are the deferral made unfalsifiable.** §5.2
describes a grant as `(resource_type, resource_id | *, action, constraint_expr)`
and nothing in this system evaluates the last two dimensions. A doc comment
saying so is a doc comment; `ck_permission_no_object` and
`ck_permission_no_constraint` mean a grant naming one object, or carrying a
constraint expression nothing reads, is unrepresentable. Zero rows exist to
violate them, and dropping the two is the whole of the migration that turns the
feature on.

**Unconditional uniqueness on `(tenant_id, lower(name))`**, not partial on
`deleted_at IS NULL`. Renaming a role is refused, so delete-and-recreate is the
sanctioned way to change a name; a partial index would free the old name and the
next role to take it would inherit every mention of it in the audit trail. The
name is tombstoned with the row.

**Operational note, and it is new.** Once `role_permission` rows exist,
`oc8 seed --reset` -- which TRUNCATEs every table in the metadata -- destroys a
tenant's authorization configuration, and `docker-compose.yml` runs `oc8 seed`
on start when `OC8_SEED_ON_START=true`. This is the first time that flag can
lose something a customer typed rather than something a fixture generated.
`--reset` needs a refusal when any non-builtin `role` row exists.

Revision ID: 0047
Revises: 0046
Create Date: 2026-08-02
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

#: The same two words as `models.core.Role.kind`'s CHECK and as the two
#: resolvers. A third kind is not a config change: it means a third population
#: reads this table, and both existing resolvers would refuse it.
_KINDS = "'human','agent'"

#: Duplicate names per tenant, which the unique index in step 5 would otherwise
#: reject with a bare `duplicate key value violates unique constraint` naming no
#: tenant and no name. `role` has carried no such constraint until now --
#: `tenants/provision.py` relies on deterministic ids instead and says so in a
#: comment -- so a deployment that ever wrote a role row by hand may well have
#: two. Not filtered on `deleted_at IS NULL`, because the index is not either.
_DUPLICATE_NAMES = """
SELECT tenant_id, lower(name) AS name, count(*) AS n
  FROM role
 GROUP BY tenant_id, lower(name)
HAVING count(*) > 1
 ORDER BY tenant_id, name
"""


def _add_constraint_if_absent(table: str, name: str, definition: str) -> None:
    """`ALTER TABLE ... ADD CONSTRAINT` has no `IF NOT EXISTS`, and the
    documented hazard in this repo (0010, 0021, 0037, 0045, 0046) makes one
    necessary: a test database is built by 0001's `create_all` over the LIVE ORM
    models for the frozen table set, and `role` and `permission` are both in it.
    So on a fresh install these constraints ALREADY exist by the time this runs,
    while on a real deployment they do not.

    Conditional ADD rather than `DROP IF EXISTS` then ADD: dropping
    `uq_role_tenant_id` would take the two composite foreign keys with it if this
    migration is ever re-run against a schema that already has them.
    """
    op.execute(
        f"""
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conname = '{name}' AND conrelid = '{table}'::regclass
          ) THEN
            ALTER TABLE {table} ADD CONSTRAINT {name} {definition};
          END IF;
        END $$
        """
    )


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
    # 1. Fail readably, before anything is altered.
    duplicates = op.get_bind().execute(sa.text(_DUPLICATE_NAMES)).all()
    if duplicates:
        offenders = ", ".join(f"{row.tenant_id}:{row.name} (x{row.n})" for row in duplicates)
        raise RuntimeError(
            "0047 cannot add UNIQUE (tenant_id, lower(name)) on `role`: these "
            f"tenants already hold a duplicated role name -- {offenders}. Merge or "
            "remove the duplicates (the row nothing references is the one to drop) "
            "and re-run. Nothing has been altered."
        )

    # 2. The four new columns on `role`.
    op.execute("ALTER TABLE role ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'human'")
    op.execute("ALTER TABLE role ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE role ADD COLUMN IF NOT EXISTS created_by uuid")
    op.execute("ALTER TABLE role ADD COLUMN IF NOT EXISTS deleted_at timestamptz")
    _add_constraint_if_absent("role", "ck_role_kind", f"CHECK (kind IN ({_KINDS}))")

    # 3. Label the agent's role row as the agent's. Idempotent, and correct for a
    #    tenant that has no such row -- `agent_default` is written by
    #    `tenants/provision.py` and by `oc8 seed`, and a tenant provisioned before
    #    either existed simply has nothing to re-label. Every OTHER role row keeps
    #    the 'human' default, which is the safe direction of the two: a row that
    #    defaults wrong grants an agent NOTHING rather than everything.
    #
    #    `AND builtin` is not decoration, and it is where this migration closes
    #    the live trap rather than carrying it forward. The only writers of a
    #    `role` row today are provisioning and the seed, and both set
    #    `builtin=true` -- so on every real deployment this term matches exactly
    #    the same rows either way. What it refuses is a HAND-WRITTEN row named
    #    `agent_default`, which is precisely the row the name-based lookup in
    #    `pdp.py` would hand all three tool rights. Left `'human'`, an agent
    #    pointed at it stops acting, visibly and reversibly, instead of acting on
    #    a name somebody chose.
    result = op.get_bind().execute(
        sa.text(
            "UPDATE role SET kind = 'agent' "
            "WHERE name = 'agent_default' AND builtin AND kind <> 'agent'"
        )
    )
    # Say the number out loud. If the migration role turns out to be subject to
    # RLS the UPDATE silently touches nothing -- fail-safe, but a 0 that means
    # "not permitted" must not be read as a 0 that means "nothing to do". A row
    # left 'human' is an agent that will be granted no tool rights the moment the
    # PDP starts reading this column.
    logger.info("0047: labelled %s agent_default role rows as kind='agent'", result.rowcount)

    # 4. The composite FK target. `(tenant_id, id)` and not `(id)`: referential
    #    integrity checks bypass RLS, so this is the only place a grant or an
    #    assignment pointing at ANOTHER tenant's role can be refused.
    _add_constraint_if_absent("role", "uq_role_tenant_id", "UNIQUE (tenant_id, id)")

    # 5. One role name per tenant, case-insensitively, forever -- see the module
    #    docstring for why this is not partial on `deleted_at IS NULL`.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_role_tenant_name ON role (tenant_id, lower(name))"
    )

    # 6. The grants themselves. Not in 0001's frozen table set, so unlike `role`
    #    this table is created here on every install, fresh or incremental.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS role_permission (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            role_id uuid NOT NULL,
            permission text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_role_permission_role FOREIGN KEY (tenant_id, role_id)
                REFERENCES role (tenant_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_role_permission_tenant_id ON role_permission (tenant_id)"
    )
    # The resolver's read, once per request for a person who holds an assigned
    # role: every permission this role grants.
    op.execute("CREATE INDEX IF NOT EXISTS ix_role_permission_role_id ON role_permission (role_id)")
    # A permission is granted or it is not; there is no second kind of grant, so
    # a duplicate row is a bug in the writer rather than history worth keeping.
    # No `tenant_id` in the key, and that is safe rather than an oversight:
    # `role_id` is a uuidv7 primary key, so it belongs to exactly one tenant
    # already, and the composite FK above proves it.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_role_permission "
        "ON role_permission (role_id, permission)"
    )
    _tenant_isolated("role_permission")

    # 7. Who holds one. NULL means "the token decides", which is every human on
    #    every live tenant and is exactly today's behaviour.
    op.execute("ALTER TABLE org_member ADD COLUMN IF NOT EXISTS role_id uuid")
    # ON DELETE RESTRICT, and it is the load-bearing half of the deletion story:
    # deleting a role somebody holds must be a 409 that names the holders. Of the
    # three available behaviours it is the only one an administrator can act on --
    # CASCADE deletes the PEOPLE rather than their assignments, and SET NULL
    # silently restores every holder to the token floor, which on the live system
    # means silently restoring forty demoted employees to `org_admin`.
    _add_constraint_if_absent(
        "org_member",
        "fk_org_member_role",
        "FOREIGN KEY (tenant_id, role_id) REFERENCES role (tenant_id, id) ON DELETE RESTRICT",
    )
    # Postgres has to scan the referencing table to enforce RESTRICT, and
    # `GET /roles` shows a holder count before an edit. Both are this index.
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_member_role_id ON org_member (role_id)")

    # 8. The deferral of §5.2's other two dimensions, enforced by Postgres
    #    instead of by a comment. Zero rows exist to violate either.
    _add_constraint_if_absent(
        "permission", "ck_permission_no_object", "CHECK (resource_id IS NULL)"
    )
    _add_constraint_if_absent(
        "permission", "ck_permission_no_constraint", "CHECK (constraint_expr = '{}'::jsonb)"
    )
    op.execute(
        """
        COMMENT ON TABLE permission IS
          'Unused. Section 5.2''s resource_id and constraint_expr await an evaluator, and the '
          'two CHECK constraints on this table make that deferral unfalsifiable rather than '
          'merely documented. Tenant grants live in role_permission, which stores the whole '
          '"resource:action" string the gate actually compares.'
        """
    )


def downgrade() -> None:
    """Lossy, and said out loud rather than left to look reversible.

    Dropping `role_permission` drops what a tenant's administrator authored, and
    dropping `org_member.role_id` drops who held it -- so downgrading past 0047
    silently restores every demoted employee to their token role, which on the
    live system is `org_admin`. That is a privilege escalation with no audit
    event, not a rollback. Re-applying 0047 afterwards brings back the empty
    tables and nothing else.

    `kind` goes too, so re-upgrading re-derives it from the name in step 3, which
    is correct for every row `provision.py` and `oc8 seed` write.
    """
    op.execute("ALTER TABLE permission DROP CONSTRAINT IF EXISTS ck_permission_no_constraint")
    op.execute("ALTER TABLE permission DROP CONSTRAINT IF EXISTS ck_permission_no_object")
    op.execute("COMMENT ON TABLE permission IS NULL")

    op.execute("DROP INDEX IF EXISTS ix_org_member_role_id")
    op.execute("ALTER TABLE org_member DROP CONSTRAINT IF EXISTS fk_org_member_role")
    op.execute("ALTER TABLE org_member DROP COLUMN IF EXISTS role_id")

    # The FK, both indexes and the policy go with the table.
    op.execute("DROP TABLE IF EXISTS role_permission")

    op.execute("DROP INDEX IF EXISTS uq_role_tenant_name")
    op.execute("ALTER TABLE role DROP CONSTRAINT IF EXISTS uq_role_tenant_id")
    op.execute("ALTER TABLE role DROP CONSTRAINT IF EXISTS ck_role_kind")
    op.execute("ALTER TABLE role DROP COLUMN IF EXISTS deleted_at")
    op.execute("ALTER TABLE role DROP COLUMN IF EXISTS created_by")
    op.execute("ALTER TABLE role DROP COLUMN IF EXISTS description")
    op.execute("ALTER TABLE role DROP COLUMN IF EXISTS kind")
