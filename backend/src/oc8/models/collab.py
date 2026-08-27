"""Cross-department collaboration: handoffs (tech-spec §14a)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from oc8.db.base import Base, TimestampMixin
from oc8.models._mixins import PkMixin, TenantMixin


class HandoffType(Base, PkMixin, TenantMixin, TimestampMixin):
    __tablename__ = "handoff_type"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    payload_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    classification: Mapped[str] = mapped_column(Text, nullable=False, default="internal")

    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_handoff_type_tenant_name"),)


class Handoff(Base, PkMixin, TenantMixin, TimestampMixin):
    __tablename__ = "handoff"

    handoff_type_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    source_department_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    target_department_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    source_task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    target_task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    flow_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    attachments: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    gate: Mapped[str] = mapped_column(Text, nullable=False, default="auto")
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','accepted','in_progress','completed','rejected','expired')",
            name="ck_handoff_status",
        ),
        CheckConstraint("gate IN ('auto','approval')", name="ck_handoff_gate"),
    )


class ContractBinding(Base, PkMixin, TenantMixin, TimestampMixin):
    __tablename__ = "contract_binding"

    event_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    handoff_type_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_department_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    target_department_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    payload_map: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    gate: Mapped[str] = mapped_column(Text, nullable=False, default="auto")

    __table_args__ = (
        CheckConstraint("gate IN ('auto','approval')", name="ck_contract_binding_gate"),
    )
