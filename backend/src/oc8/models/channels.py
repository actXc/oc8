"""Approval channels: which messenger account belongs to which oc8 user (§5.6)."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from oc8.db.base import Base, TimestampMixin
from oc8.models._mixins import PkMixin, TenantMixin


class ApprovalChannelBinding(Base, PkMixin, TenantMixin, TimestampMixin):
    """A messenger account an approver may answer from — or a code waiting to
    become one.

    Both states live here, told apart by `external_id`: NULL means a code has
    been issued and not yet redeemed, set means the account is live. A code IS a
    binding waiting for its account, so splitting the two would only add a
    transaction that moves a row between tables.

    Nothing here is ever inferred. A chat id is not a user and a phone number
    proves nothing, so the link is made by an authenticated person who asks for
    a code and sends it to the bot — and can be taken away again by setting
    `revoked_at`.
    """

    __tablename__ = "approval_channel_binding"

    #: Which channel plugin this belongs to ("telegram", "whatsapp", …). Kept as
    #: text rather than an enum: core must not know which messengers exist.
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: `org_member.id` of the person this binding speaks for, written at issue
    #: time from the authenticated caller.
    #:
    #: **A binding with `member_id IS NULL` decides nothing.** Nullable because
    #: bindings issued before this column existed have no honest answer, and
    #: grandfathering them by inventing an `org_member` row per binding would
    #: collide with `uq_org_member_subject_uuid` the first time that same human
    #: authenticated -- 500-ing every request from exactly the population
    #: piloting the feature. They are re-issued through
    #: `POST /channels/{channel}/link` instead; on the live system that is one
    #: Telegram binding.
    member_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    #: The account as the far platform names it (a Telegram chat id, a WhatsApp
    #: wa_id). Opaque to core on purpose.
    external_id: Mapped[str | None] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(Text)
    code_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class ChannelPollCursor(Base, PkMixin, TenantMixin, TimestampMixin):
    """Where a poll-based channel (one with no public webhook URL to receive,
    e.g. `telegram_approvals` running with no tunnel) left off reading the
    platform's own update queue -- one row per (tenant, channel).

    `last_update_id` is the platform's own monotonically increasing cursor
    (Telegram's `update_id`); a fresh row starts at 0, which every platform's
    "give me everything from here" convention treats as "the beginning."
    """

    __tablename__ = "channel_poll_cursor"

    #: Same free-form channel id as `ApprovalChannelBinding.channel` -- core
    #: must not know which messengers exist.
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    last_update_id: Mapped[int] = mapped_column(nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("tenant_id", "channel", name="uq_channel_poll_cursor_tenant_channel"),
    )
