"""Tenant secret store: wrapped per-tenant DEKs and encrypted secrets (tech-spec §12.3)."""

from __future__ import annotations

from sqlalchemy import LargeBinary, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from oc8.db.base import Base, TimestampMixin
from oc8.models._mixins import PkMixin, TenantMixin


class TenantDek(Base, PkMixin, TenantMixin, TimestampMixin):
    __tablename__ = "tenant_dek"

    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("tenant_id", name="uq_tenant_dek_tenant"),)


class Secret(Base, PkMixin, TenantMixin, TimestampMixin):
    __tablename__ = "secret"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="generic")

    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_secret_tenant_name"),)
