"""Global, versioned model pricing (Cost Center design, 2026-08-19 spec Part A)."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from oc8.db.base import Base
from oc8.models._mixins import PkMixin, TenantMixin


class ModelPrice(Base, PkMixin):
    """A single price version for one (provider, model_pattern). GLOBAL, not
    tenant-scoped -- provider token prices are identical for every tenant.
    "Editing" a price never updates a row; it inserts a new one with a newer
    effective_from, so a report for a date before the edit keeps using the
    price that was actually in effect then. "Deleting" sets active=false on a
    NEW row (never a real DELETE), for the same reason."""

    __tablename__ = "model_price"

    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    price_in_usd_per_1m: Mapped[float] = mapped_column(Numeric, nullable=False)
    price_out_usd_per_1m: Mapped[float] = mapped_column(Numeric, nullable=False)
    effective_from: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_model_price_provider_pattern_effective",
            "provider",
            "model_pattern",
            "effective_from",
        ),
    )


class ModelCostReconciliation(Base, PkMixin, TenantMixin):
    """One provider's reported daily cost vs. oc8's own token x price
    calculation for the same day. Anthropic + OpenAI only -- Mistral has no
    cost-reporting API and gets no row here, ever. On-demand fetch only, no
    scheduled job (spec's explicit v1 scope)."""

    __tablename__ = "model_cost_reconciliation"

    provider: Mapped[str] = mapped_column(Text, nullable=False)
    report_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    oc8_calculated_cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider_reported_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "provider", "report_date", name="uq_model_cost_reconciliation"
        ),
    )
