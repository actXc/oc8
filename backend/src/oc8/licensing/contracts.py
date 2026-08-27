"""Neutral entitlement contract and the Community-only implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from oc8.licensing.capabilities import Capability


class Entitlements(Protocol):
    """Capabilities and optional numeric limits authorized for this deployment."""

    def has(self, capability: Capability) -> bool:
        """Whether a capability is authorized."""
        ...

    def limit(self, name: str) -> int | None:
        """Return an authorized limit, or ``None`` if no limit is granted."""
        ...


@dataclass(frozen=True, slots=True)
class CommunityEntitlements:
    """The immutable entitlement set for Community-only deployments.

    Community features are not represented as license capabilities.  Therefore
    this implementation never grants an Enterprise capability or a paid limit.
    """

    def has(self, capability: Capability) -> bool:
        return False

    def limit(self, name: str) -> int | None:
        return None
