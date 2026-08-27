"""guard organization SELECT RLS against pooled-connection GUC reset (§8.4)

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-17

0013's `tenant_isolation_select` policy treats an unbound session as
`current_setting('app.tenant_id', true) IS NULL`. That holds the FIRST time a
custom (non-compiled-in) GUC like `app.tenant_id` is ever touched on a given
physical connection. But `set_config(..., is_local => true)` is transaction-
local: once a transaction on that connection binds a real tenant_id and then
commits, Postgres does not revert the custom GUC to true NULL -- it reverts to
`''` (empty string), the placeholder's reset value, for the remaining life of
that physical connection. On a connection-pooled engine (the Trigger
Service's scheduler/webhook-handler engine, which loops forever calling
`list_active_tenant_ids()` -- an unbound discovery read -- between per-tenant
bound fires), the SECOND unbound read on a connection that was previously
tenant-bound sees `''`, not NULL: `IS NULL` is false, so the policy falls
through to `id = current_setting(...)::uuid`, and casting `''` to uuid raises
`invalid input syntax for type uuid: ""` instead of cleanly permitting (or
denying) the row. In production this wedges the scheduler tick's tenant-
discovery step after its first successful fire on a primed connection.

The fix: treat `''` as unbound too, via `NULLIF(..., '') IS NULL`, guarding
BOTH sides of the OR -- Postgres does not guarantee left-to-right
short-circuit evaluation of OR in a RLS USING clause, so the cast side must
be guarded as well, not just the null-check side.

Scope: the SELECT policy only. INSERT/UPDATE/DELETE on `organization`, and
every other tenant-scoped table's RLS policies, are only ever evaluated
inside a bound tenant_session (a real uuid), so they never observe the ''
reset value and don't need this guard.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_select ON organization")
    op.execute(
        "CREATE POLICY tenant_isolation_select ON organization "
        "FOR SELECT USING ("
        "NULLIF(current_setting('app.tenant_id', true), '') IS NULL "
        "OR id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
        ")"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_select ON organization")
    op.execute(
        "CREATE POLICY tenant_isolation_select ON organization "
        "FOR SELECT USING ("
        "current_setting('app.tenant_id', true) IS NULL "
        "OR id = current_setting('app.tenant_id', true)::uuid"
        ")"
    )
