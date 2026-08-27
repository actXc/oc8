"""Trigger Service (§8.4): cron-schedule, source-specific event, and generic
webhook trigger rows."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import CheckConstraint, DateTime, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from oc8.db.base import Base, TimestampMixin
from oc8.models._mixins import PkMixin, TenantMixin


class Trigger(Base, PkMixin, TenantMixin, TimestampMixin):
    """Discriminated union: kind='cron' rows carry cron_expression and
    nothing else; kind='event' rows carry event_source/event_type (a signed,
    source-specific push -- GitHub today) and no cron_expression/
    webhook_token; kind='webhook' rows carry webhook_token (a generic,
    n8n-Webhook-node-style trigger -- ANY caller that can POST JSON to its
    one unguessable URL fires it, no per-source adapter code, no signature).
    Enforced by ck_trigger_kind_fields below."""

    __tablename__ = "trigger"

    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    task_text: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)

    cron_expression: Mapped[str | None] = mapped_column(Text)
    next_run_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    event_source: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str | None] = mapped_column(Text)

    # The entire auth boundary for a kind='webhook' row: a high-entropy
    # (32-byte urlsafe, ~256 bits) random token, unique across every tenant
    # so POST /webhooks/{token} can look a row up with no tenant context yet
    # (mirrors handle_inbound_event's own all-tenant discovery loop, since
    # RLS means no unbound query can filter by tenant first).
    webhook_token: Mapped[str | None] = mapped_column(Text, unique=True)

    __table_args__ = (
        CheckConstraint("kind IN ('cron','event','webhook')", name="ck_trigger_kind"),
        CheckConstraint(
            "(kind = 'cron' AND cron_expression IS NOT NULL "
            "AND event_source IS NULL AND event_type IS NULL AND webhook_token IS NULL) "
            "OR (kind = 'event' AND event_source IS NOT NULL AND event_type IS NOT NULL "
            "AND cron_expression IS NULL AND webhook_token IS NULL) "
            "OR (kind = 'webhook' AND webhook_token IS NOT NULL "
            "AND cron_expression IS NULL AND event_source IS NULL AND event_type IS NULL)",
            name="ck_trigger_kind_fields",
        ),
    )
