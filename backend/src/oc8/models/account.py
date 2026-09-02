"""Single-use, time-limited, mailed tokens backing the account self-service
flows that require proving control of an email address (design:
docs/superpowers/specs/2026-08-28-account-self-service-design.md §4.4) --
forgot-password, email-change confirmation, and (migration 0084) the invite
link `POST /members` mints for a person created with no password. One table
for all three: they are the same shape (a token tied to a member and a
purpose), and no generic token table existed yet to reuse.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import CheckConstraint, DateTime, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from oc8.db.base import Base, TimestampMixin
from oc8.models._mixins import PkMixin, TenantMixin


class AccountVerificationToken(Base, PkMixin, TenantMixin, TimestampMixin):
    __tablename__ = "account_verification_token"

    member_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    #: sha256(token), hex-encoded. The plaintext token is mailed once and
    #: never stored -- this column exists only to look the token back up
    #: when it comes back on a confirm/reset request.
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    #: Only set for purpose == "email_change" -- the address the token, once
    #: confirmed, rewrites `org_member.subject` to.
    new_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "purpose IN ('password_reset','email_change','invite')",
            name="ck_account_verification_token_purpose",
        ),
    )
