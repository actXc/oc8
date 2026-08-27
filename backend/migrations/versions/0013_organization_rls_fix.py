"""narrow organization RLS to allow tenant-discovery reads (§8.4)

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-17

0001's `tenant_isolation` policy on `organization` used a single combined
policy: `USING (id = current_setting('app.tenant_id', true)::uuid)`. When
`app.tenant_id` is unbound (tenant_session(None), e.g. the Trigger Service
scheduler discovering every tenant), `current_setting(...)` returns NULL and
`id = NULL` is never true -- so an unbound session saw *zero* organization
rows, not all of them. This broke cross-tenant tenant-discovery entirely.

The fix is scoped narrowly: SELECT is permitted when either the session is
unbound (NULL, discovery case) or scoped to that tenant's own row (normal
case, unchanged). Mutations still strictly require id = the bound tenant_id
-- an unbound session's id = NULL comparison is never true on INSERT/UPDATE/
DELETE, so it can read broadly but never write. A normal tenant-bound
session's own visibility is completely unchanged: it still only ever sees
its own row.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON organization")
    op.execute(
        "CREATE POLICY tenant_isolation_select ON organization "
        "FOR SELECT USING ("
        "current_setting('app.tenant_id', true) IS NULL "
        "OR id = current_setting('app.tenant_id', true)::uuid"
        ")"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON organization "
        "FOR INSERT WITH CHECK (id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_update ON organization "
        "FOR UPDATE USING (id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_delete ON organization "
        "FOR DELETE USING (id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_select ON organization")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON organization")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_update ON organization")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_delete ON organization")
    op.execute(
        "CREATE POLICY tenant_isolation ON organization "
        "USING (id = current_setting('app.tenant_id', true)::uuid)"
    )
