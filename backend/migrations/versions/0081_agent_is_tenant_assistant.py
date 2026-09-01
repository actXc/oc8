# backend/migrations/versions/0081_agent_is_tenant_assistant.py
"""Add is_tenant_assistant flag to agent.

Marks the ONE agent per tenant that is the unified oc8 Assistant (design doc
2026-08-31-unified-assistant-telegram-chat-design.md). Separate from
is_team_lead: every is_tenant_assistant agent is also is_team_lead, but the
reverse is not true -- ordinary department team leads must NOT get this
agent's cross-department delegation reach (see control_tools.py's
_delegate() guard). IF NOT EXISTS: `agent` is one of the tables 0001_initial.py
creates via a live Base.metadata.create_all(), so this column already exists
on a fresh database by the time 0001 finishes.

Revision ID: 0081
Revises: 0080
Create Date: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0081"
down_revision: str | None = "0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent ADD COLUMN IF NOT EXISTS "
        "is_tenant_assistant boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent DROP COLUMN IF EXISTS is_tenant_assistant")
