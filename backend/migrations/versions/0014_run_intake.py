"""agent_run intake columns: source, idempotency_key, coalesce_key,
coalesced_count (§14.1/§8.3)

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_run",
        sa.Column("source", sa.Text(), nullable=False, server_default="manual"),
    )
    op.add_column("agent_run", sa.Column("idempotency_key", sa.Text(), nullable=True))
    op.add_column("agent_run", sa.Column("coalesce_key", sa.Text(), nullable=True))
    op.add_column(
        "agent_run",
        sa.Column("coalesced_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_agent_run_source",
        "agent_run",
        "source IN ('manual','cron','event')",
    )
    op.create_index(
        "uq_agent_run_idempotency",
        "agent_run",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_agent_run_coalesce", "agent_run", ["tenant_id", "agent_id", "coalesce_key"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_run_coalesce", table_name="agent_run")
    op.drop_index("uq_agent_run_idempotency", table_name="agent_run")
    op.drop_constraint("ck_agent_run_source", "agent_run", type_="check")
    op.drop_column("agent_run", "coalesced_count")
    op.drop_column("agent_run", "coalesce_key")
    op.drop_column("agent_run", "idempotency_key")
    op.drop_column("agent_run", "source")
