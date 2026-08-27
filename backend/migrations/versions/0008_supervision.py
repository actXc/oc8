"""supervision tables with RLS (§8.6)

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = dict(server_default=sa.func.now(), nullable=False)


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def upgrade() -> None:
    op.create_table(
        "supervision_policy",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_every", JSONB(), nullable=False, server_default="{}"),
        sa.Column("drift_thresholds", JSONB(), nullable=False, server_default="{}"),
        sa.Column("allowed_interventions", JSONB(), nullable=False, server_default="[]"),
        sa.Column("judge_model_config_id", sa.Uuid(), nullable=True),
        sa.Column("sampling_rate", sa.Numeric(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )
    op.create_index("ix_supervision_policy_tenant_id", "supervision_policy", ["tenant_id"])
    op.create_index("ix_supervision_policy_department_id", "supervision_policy", ["department_id"])
    _rls("supervision_policy")

    op.create_table(
        "supervision_assignment",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("supervisor_agent_id", sa.Uuid(), nullable=False),
        sa.Column("supervised_agent_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.UniqueConstraint("supervised_agent_id", name="uq_supervision_supervised"),
    )
    op.create_index("ix_supervision_assignment_tenant_id", "supervision_assignment", ["tenant_id"])
    op.create_index(
        "ix_supervision_assignment_supervisor", "supervision_assignment", ["supervisor_agent_id"]
    )
    _rls("supervision_assignment")

    op.create_table(
        "task_anchor",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("acceptance_criteria", JSONB(), nullable=False, server_default="{}"),
        sa.Column("constraints", JSONB(), nullable=False, server_default="{}"),
        sa.Column("anchor_embedding", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.UniqueConstraint("task_id", name="uq_task_anchor_task"),
    )
    op.create_index("ix_task_anchor_tenant_id", "task_anchor", ["tenant_id"])
    _rls("task_anchor")

    op.create_table(
        "agent_checkpoint",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("state_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("context_ref", sa.Text(), nullable=True),
        sa.Column("drift_score", sa.Numeric(), nullable=True),
        sa.Column("verdict", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.UniqueConstraint("agent_id", "task_id", "seq", name="uq_agent_checkpoint_seq"),
        sa.CheckConstraint(
            "verdict IS NULL OR verdict IN ('on_track','drifting','off_track')",
            name="ck_agent_checkpoint_verdict",
        ),
    )
    op.create_index("ix_agent_checkpoint_tenant_id", "agent_checkpoint", ["tenant_id"])
    op.create_index("ix_agent_checkpoint_agent_id", "agent_checkpoint", ["agent_id"])
    op.create_index("ix_agent_checkpoint_task_id", "agent_checkpoint", ["task_id"])
    _rls("agent_checkpoint")

    op.create_table(
        "supervision_intervention",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("supervisor_agent_id", sa.Uuid(), nullable=False),
        sa.Column("supervised_agent_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("reason", JSONB(), nullable=False, server_default="{}"),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.CheckConstraint(
            "kind IN ('steer','rewind','pause_escalate','reassign')",
            name="ck_supervision_intervention_kind",
        ),
    )
    op.create_index(
        "ix_supervision_intervention_tenant_id", "supervision_intervention", ["tenant_id"]
    )
    op.create_index(
        "ix_supervision_intervention_supervised",
        "supervision_intervention",
        ["supervised_agent_id"],
    )
    _rls("supervision_intervention")


def downgrade() -> None:
    op.drop_table("supervision_intervention")
    op.drop_table("agent_checkpoint")
    op.drop_table("task_anchor")
    op.drop_table("supervision_assignment")
    op.drop_table("supervision_policy")
