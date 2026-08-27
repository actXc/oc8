"""Well-known fixed identifiers used by seed data and dev tooling."""

from __future__ import annotations

import uuid

# Deterministic tenant ids so dev-login and the seed agree without a lookup.
ACME_TENANT_ID = uuid.UUID("0192a000-0000-7000-8000-000000000001")
GLOBEX_TENANT_ID = uuid.UUID("0192a000-0000-7000-8000-000000000002")

DEV_OPERATOR_SUBJECT = "dev-operator"

CORE_VERSION = "1.0.0"
"""Plugin core-compat baseline; manifests declare a range against this (§13.3)."""
