"""Direct-chat feature: chat_session + chat_message tables, and 'chat' added
as a valid agent_run.source value (each chat turn is a real AgentRun so it
suspends/resumes on approval exactly like an autonomous run).

Revision ID: 0077
Revises: 0076
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0077"
down_revision: str | None = "0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = dict(server_default=sa.func.now(), nullable=False)


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def upgrade() -> None:
    op.execute("ALTER TABLE agent_run DROP CONSTRAINT ck_agent_run_source")
    op.execute(
        "ALTER TABLE agent_run ADD CONSTRAINT ck_agent_run_source "
        "CHECK (source IN "
        "('manual','cron','event','webhook','delegation','decision','handoff','chat'))"
    )

    op.create_table(
        "chat_session",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )
    op.create_index("ix_chat_session_tenant_id", "chat_session", ["tenant_id"])
    op.create_index("ix_chat_session_agent_id", "chat_session", ["agent_id"])
    op.create_index("ix_chat_session_member_id", "chat_session", ["member_id"])
    _rls("chat_session")

    op.create_table(
        "chat_message",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("rendered_components", JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.CheckConstraint("role IN ('user','assistant')", name="ck_chat_message_role"),
    )
    op.create_index("ix_chat_message_tenant_id", "chat_message", ["tenant_id"])
    op.create_index("ix_chat_message_session_id", "chat_message", ["session_id"])
    op.create_index("ix_chat_message_run_id", "chat_message", ["run_id"])
    _rls("chat_message")


def downgrade() -> None:
    op.drop_table("chat_message")
    op.drop_table("chat_session")
    op.execute("ALTER TABLE agent_run DROP CONSTRAINT ck_agent_run_source")
    op.execute(
        "ALTER TABLE agent_run ADD CONSTRAINT ck_agent_run_source "
        "CHECK (source IN "
        "('manual','cron','event','webhook','delegation','decision','handoff'))"
    )
