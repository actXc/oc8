"""a plugin can be the channel an approval is decided on, not just what an agent runs

0009 enumerated the plugin types the database would accept; §13.3's manifest schema
(oc8.plugins.manifest.PluginType) grew a tenth one, `approval_channel`, for
Telegram/WhatsApp-style plugins that render an ApprovalNotice and turn a button
press back into a ChannelDecision. The manifest side parsed it fine -- discovery
never touched the database -- so the gap stayed invisible until the first real
install: `POST /plugins/install-from-disk` on `telegram_approvals` or
`whatsapp_approvals` 500'd on `ck_plugin_type`, because the row it tried to
persist named a type the constraint had never heard of.

Revision ID: 0049
Revises: 0048
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_TYPES = (
    "'skill','flow_template','agent_template','department_template',"
    "'connector','model_adapter','runtime_adapter','tool_pack','core_extension'"
)
_NEW_TYPES = (
    "'skill','flow_template','agent_template','department_template',"
    "'connector','model_adapter','runtime_adapter','tool_pack',"
    "'approval_channel','core_extension'"
)


def upgrade() -> None:
    op.drop_constraint("ck_plugin_type", "plugin", type_="check")
    op.create_check_constraint("ck_plugin_type", "plugin", f"type IN ({_NEW_TYPES})")


def downgrade() -> None:
    op.drop_constraint("ck_plugin_type", "plugin", type_="check")
    op.create_check_constraint("ck_plugin_type", "plugin", f"type IN ({_OLD_TYPES})")
