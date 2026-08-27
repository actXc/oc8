"""§14.2 CloudEvents-compatible event envelope builder (pure)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from oc8.db.base import uuid7


def build_envelope(
    tenant_id: uuid.UUID, type_: str, data: dict[str, Any], source: str
) -> dict[str, Any]:
    """A §14.2 envelope. `id` (uuid7) is the client dedup key."""
    return {
        "specversion": "1.0",
        "id": str(uuid7()),
        "source": source,
        "type": type_,
        "time": datetime.now(tz=UTC).isoformat(),
        "tenantid": str(tenant_id),
        "data": data,
    }
