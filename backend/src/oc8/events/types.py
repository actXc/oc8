"""Normalized inbound event shape."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InboundEvent:
    source: str
    type: str
    payload: dict[str, Any]
    delivery_id: str | None = None
