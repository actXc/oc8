"""Persistent, deliberately secret-free Copilot proposal records."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, Integer, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from oc8.db.base import Base, TimestampMixin
from oc8.models._mixins import PkMixin, TenantMixin


class CopilotProposal(Base, PkMixin, TenantMixin, TimestampMixin):
    __tablename__ = "copilot_proposal"

    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    applied_by: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, default=uuid.uuid4)

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','applied','expired','rejected')",
            name="ck_copilot_proposal_status",
        ),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_copilot_proposal_idempotency"
        ),
    )

    def as_json(self) -> dict[str, Any]:
        """Review representation: labels/references only, never operation data."""
        return {
            "id": str(self.id),
            "status": self.status,
            "revision": self.revision,
            "operations": [],
        }


class CopilotOperation(Base, PkMixin, TenantMixin, TimestampMixin):
    __tablename__ = "copilot_operation"

    proposal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Validated typed values only.  This must not be reused as an arbitrary
    # request payload container; capabilities.py is its sole writer.
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    target_revision: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("proposal_id", "ordinal", name="uq_copilot_operation_order"),
    )
