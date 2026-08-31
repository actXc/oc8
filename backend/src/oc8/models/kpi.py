"""KPI-supporting tables — see backend/src/oc8/kpis/aggregate.py for what
reads them."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from oc8.db.base import Base
from oc8.models._mixins import PkMixin, TenantMixin


class RunStateTransition(Base, PkMixin, TenantMixin):
    """One row per AgentRun state change, written by
    RunRepository.transition() in the same transaction as the state write
    itself. from_state is NULL only for the very first transition into
    'queued' (there is no prior state)."""

    __tablename__ = "run_state_transition"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_run.id"), nullable=False)
    from_state: Mapped[str | None] = mapped_column(Text(), nullable=True)
    to_state: Mapped[str] = mapped_column(Text(), nullable=False)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
