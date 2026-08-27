"""Web Push subscriptions -- one row per browser/device an operator has
opted into push notifications on."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from oc8.db.base import Base, TimestampMixin
from oc8.models._mixins import PkMixin, TenantMixin


class PushSubscription(Base, PkMixin, TenantMixin, TimestampMixin):
    """A Web Push subscription for one operator's browser/device.

    `(tenant_id, endpoint)` is unique, and `endpoint` alone is NOT. The push
    service assigns one endpoint per browser installation against this
    instance's single VAPID key pair -- it knows nothing about tenants -- so
    the same browser presents the SAME endpoint under every tenant its user
    belongs to. A global unique constraint would make the second tenant's
    subscribe an IntegrityError that no tenant-scoped SELECT could have seen
    coming, because RLS hides the other tenant's row from the lookup that is
    supposed to detect it.

    Scoped per tenant it is still the natural upsert key: re-subscribing the
    same browser under the same tenant (e.g. after toggling off and back on)
    updates the existing row instead of creating a duplicate.
    """

    __tablename__ = "push_subscription"

    member_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("org_member.id", ondelete="CASCADE"), nullable=False
    )
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "endpoint", name="uq_push_subscription_endpoint"),
    )
