"""supervision tier-2 judge columns (§8.6.2)

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-20
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_checkpoint ADD COLUMN IF NOT EXISTS judge_verdict text")
    op.execute("ALTER TABLE agent_checkpoint ADD COLUMN IF NOT EXISTS judge_evidence jsonb")
    op.execute(
        "ALTER TABLE agent_checkpoint DROP CONSTRAINT IF EXISTS ck_agent_checkpoint_judge_verdict"
    )
    op.execute(
        "ALTER TABLE agent_checkpoint ADD CONSTRAINT ck_agent_checkpoint_judge_verdict "
        "CHECK (judge_verdict IS NULL OR judge_verdict IN ('on_track','drifting','off_track'))"
    )
    op.execute("ALTER TABLE supervision_policy ADD COLUMN IF NOT EXISTS judge_rubric jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE supervision_policy DROP COLUMN IF EXISTS judge_rubric")
    op.execute(
        "ALTER TABLE agent_checkpoint DROP CONSTRAINT IF EXISTS ck_agent_checkpoint_judge_verdict"
    )
    op.execute("ALTER TABLE agent_checkpoint DROP COLUMN IF EXISTS judge_evidence")
    op.execute("ALTER TABLE agent_checkpoint DROP COLUMN IF EXISTS judge_verdict")
