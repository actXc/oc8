"""skill_assignment reaches a department or the whole tenant, not just one agent

A skill could only be given to a single agent. A standard set for a company
therefore meant one assignment per skill per agent, redone by hand every time
somebody was hired -- and the last four skills were assigned exactly that way,
twice, because there are two agents.

The scope is expressed by which column is filled, so an existing row means what
it always meant:

    agent_id set                     -> this agent
    department_id set                -> every agent in that department
    neither                          -> every agent of the tenant

Two partial unique indexes rather than one constraint: in Postgres NULLs are
distinct, so a single UNIQUE (agent_id, skill_version_id) would happily accept
the same department-wide row a hundred times.

Revision ID: 0040
Revises: 0039
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE skill_assignment ALTER COLUMN agent_id DROP NOT NULL")
    op.execute("ALTER TABLE skill_assignment ADD COLUMN IF NOT EXISTS department_id uuid")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_skill_assignment_department_id "
        "ON skill_assignment (department_id)"
    )
    # Exactly one of the two may be set. A row naming both would be ambiguous:
    # it reads as "this agent" and as "everyone here" at the same time.
    op.execute(
        "ALTER TABLE skill_assignment DROP CONSTRAINT IF EXISTS ck_skill_assignment_scope"
    )
    op.execute(
        "ALTER TABLE skill_assignment ADD CONSTRAINT ck_skill_assignment_scope "
        "CHECK (agent_id IS NULL OR department_id IS NULL)"
    )
    # The old constraint covered agent rows only and cannot express the rest.
    op.execute("ALTER TABLE skill_assignment DROP CONSTRAINT IF EXISTS uq_skill_assignment")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_assignment_agent "
        "ON skill_assignment (agent_id, skill_version_id) WHERE agent_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_assignment_department "
        "ON skill_assignment (department_id, skill_version_id) "
        "WHERE department_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_assignment_tenant "
        "ON skill_assignment (tenant_id, skill_version_id) "
        "WHERE agent_id IS NULL AND department_id IS NULL"
    )


def downgrade() -> None:
    op.execute("DELETE FROM skill_assignment WHERE agent_id IS NULL")
    op.execute("DROP INDEX IF EXISTS uq_skill_assignment_tenant")
    op.execute("DROP INDEX IF EXISTS uq_skill_assignment_department")
    op.execute("DROP INDEX IF EXISTS uq_skill_assignment_agent")
    op.execute(
        "ALTER TABLE skill_assignment DROP CONSTRAINT IF EXISTS ck_skill_assignment_scope"
    )
    op.execute("ALTER TABLE skill_assignment DROP COLUMN IF EXISTS department_id")
    op.execute("ALTER TABLE skill_assignment ALTER COLUMN agent_id SET NOT NULL")
    op.execute(
        "ALTER TABLE skill_assignment ADD CONSTRAINT uq_skill_assignment "
        "UNIQUE (agent_id, skill_version_id)"
    )
