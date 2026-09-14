"""Add ApprovalRequest.reason_context.

The structured sibling of `detail`: `{"code": <Decision.reason_code>,
**Decision.context}` when an approval was raised from a PDP
`authorize_tool_call` REQUIRE_APPROVAL decision (see the column's own
docstring in models/ops.py). Nullable and additive -- every existing row and
every approval NOT raised from such a decision keeps `detail`/`amount_text`
as its only "why", exactly as before.

Revision ID: 0092
Revises: 0091
Create Date: 2026-09-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0092"
down_revision: str | None = "0091"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE approval_request ADD COLUMN IF NOT EXISTS reason_context jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE approval_request DROP COLUMN IF EXISTS reason_context")
