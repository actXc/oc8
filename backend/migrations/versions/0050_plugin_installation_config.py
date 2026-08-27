"""a tenant's answers to a plugin's setup form, when there is no connection to hold them

0009 gave every McpConnection a `config` JSONB column, and 0049's `setup`
contract has been writing non-secret field values there ever since -- but only
because a tool_pack's setup always ends in one. An approval_channel has no
connection: channels/registry.py resolves its credential straight off the
manifest's own `[plugin.config]`, which is fine for a value the plugin author
fixes (a classification ceiling) and wrong for one that varies by tenant (a
WhatsApp Business phone number). Nothing before this migration had anywhere to
put that second kind of value once collected.

Revision ID: 0050
Revises: 0049
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plugin_installation",
        sa.Column("config", JSONB(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("plugin_installation", "config")
