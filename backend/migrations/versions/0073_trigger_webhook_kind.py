"""trigger gains kind='webhook' -- a generic, n8n-Webhook-node-style trigger:
one unguessable token per row is the entire auth boundary, no per-source
signature/adapter code (unlike kind='event', which stays GitHub-specific
today). agent_run.source gains 'webhook' to match -- fire_trigger() passes
trigger.kind straight through as the run's source, and a webhook-kind fire
would otherwise fail ck_agent_run_source's own check. §8.4 generic webhooks.

Revision ID: 0073
Revises: 0072
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0073"
down_revision: str | None = "0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUN_SOURCES_BEFORE = "'manual','cron','event','delegation','decision','handoff'"
_RUN_SOURCES_AFTER = "'manual','cron','event','webhook','delegation','decision','handoff'"


def upgrade() -> None:
    op.add_column("trigger", sa.Column("webhook_token", sa.Text(), nullable=True))
    op.create_unique_constraint("uq_trigger_webhook_token", "trigger", ["webhook_token"])
    op.drop_constraint("ck_trigger_kind", "trigger", type_="check")
    op.drop_constraint("ck_trigger_kind_fields", "trigger", type_="check")
    op.create_check_constraint("ck_trigger_kind", "trigger", "kind IN ('cron','event','webhook')")
    op.create_check_constraint(
        "ck_trigger_kind_fields",
        "trigger",
        "(kind = 'cron' AND cron_expression IS NOT NULL "
        "AND event_source IS NULL AND event_type IS NULL AND webhook_token IS NULL) "
        "OR (kind = 'event' AND event_source IS NOT NULL AND event_type IS NOT NULL "
        "AND cron_expression IS NULL AND webhook_token IS NULL) "
        "OR (kind = 'webhook' AND webhook_token IS NOT NULL "
        "AND cron_expression IS NULL AND event_source IS NULL AND event_type IS NULL)",
    )
    op.execute("ALTER TABLE agent_run DROP CONSTRAINT IF EXISTS ck_agent_run_source")
    op.execute(
        "ALTER TABLE agent_run ADD CONSTRAINT ck_agent_run_source "
        f"CHECK (source = ANY (ARRAY[{_RUN_SOURCES_AFTER}]))"
    )


def downgrade() -> None:
    op.execute("UPDATE agent_run SET source = 'event' WHERE source = 'webhook'")
    op.execute("ALTER TABLE agent_run DROP CONSTRAINT IF EXISTS ck_agent_run_source")
    op.execute(
        "ALTER TABLE agent_run ADD CONSTRAINT ck_agent_run_source "
        f"CHECK (source = ANY (ARRAY[{_RUN_SOURCES_BEFORE}]))"
    )
    op.drop_constraint("ck_trigger_kind_fields", "trigger", type_="check")
    op.drop_constraint("ck_trigger_kind", "trigger", type_="check")
    op.create_check_constraint("ck_trigger_kind", "trigger", "kind IN ('cron','event')")
    op.create_check_constraint(
        "ck_trigger_kind_fields",
        "trigger",
        "(kind = 'cron' AND cron_expression IS NOT NULL "
        "AND event_source IS NULL AND event_type IS NULL) "
        "OR (kind = 'event' AND event_source IS NOT NULL AND event_type IS NOT NULL "
        "AND cron_expression IS NULL)",
    )
    op.drop_constraint("uq_trigger_webhook_token", "trigger", type_="unique")
    op.drop_column("trigger", "webhook_token")
