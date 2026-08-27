"""Community licensing contracts remain unlicensed and immutable."""

from __future__ import annotations

import pytest

from oc8.licensing import CommunityEntitlements


def test_community_entitlements_grant_no_enterprise_capabilities() -> None:
    entitlements = CommunityEntitlements()

    assert entitlements.has("enterprise.example") is False
    assert entitlements.limit("active_agents") is None
    with pytest.raises((AttributeError, TypeError)):
        entitlements.anything = "else"  # type: ignore[misc]
