"""Community-owned licensing contracts.

The Community package provides neutral entitlement types but never imports an
Enterprise verifier or makes Enterprise licensing decisions.
"""

from oc8.licensing.contracts import CommunityEntitlements, Entitlements

__all__ = ["CommunityEntitlements", "Entitlements"]
