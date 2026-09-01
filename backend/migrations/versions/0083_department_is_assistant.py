# backend/migrations/versions/0083_department_is_assistant.py
"""Add is_assistant_department flag to department.

The oc8 Assistant's department is auto-provisioned and not user-configurable,
so it must not show up in the Office floor view, the Departments list, or any
other place a person browses departments. `Agent.is_tenant_assistant` already
answers that question for the agent; `Department` had no equivalent, and the
scoped repositories (`departments/repo.py`) need one they can put in a WHERE
clause. Mirrors 0081 in shape for exactly that reason.

Backfilled from the agent side so existing tenants -- whose Assistant was
lazily provisioned before this column existed -- hide it too.

IF NOT EXISTS: `department` is one of the tables 0001_initial.py creates via a
live Base.metadata.create_all(), so this column already exists on a fresh
database by the time 0001 finishes.

Revision ID: 0083
Revises: 0082
Create Date: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0083"
down_revision: str | None = "0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE department ADD COLUMN IF NOT EXISTS "
        "is_assistant_department boolean NOT NULL DEFAULT false"
    )
    op.execute(
        """
        UPDATE department SET is_assistant_department = true
        WHERE NOT is_assistant_department AND id IN (
            SELECT department_id FROM agent WHERE is_tenant_assistant
        )
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE department DROP COLUMN IF EXISTS is_assistant_department")
