"""Shared column mixins for ORM models."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from oc8.db.base import uuid7


class PkMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)


class TenantMixin:
    """Marks a table as tenant-scoped. RLS policies are added in migration 0001
    for every table carrying this column."""

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)


class SoftDeleteMixin:
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
