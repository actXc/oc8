"""Write-time attribution: resolve the principal ultimately responsible for an
audited event via a fallback chain (§12.5 A3). Pure -- no DB, cannot fail."""

from __future__ import annotations

import uuid

from oc8.auth import Principal

ResponsiblePrincipal = tuple[str, str]  # (type, id-as-text)


def resolve_responsible(
    *,
    tenant_id: uuid.UUID,
    actor_type: str,
    actor_id: uuid.UUID | None,
    principal: Principal | None = None,
    originating_operator: str | None = None,
) -> ResponsiblePrincipal:
    """User-Actor -> Run/Task-originating operator -> Agent/Plugin token ->
    Tenant-Default. First match wins; the tenant default guarantees non-null."""
    if principal is not None and principal.kind == "operator":
        return ("operator", principal.subject)
    if originating_operator is not None:
        return ("operator", originating_operator)
    if actor_type in ("agent", "plugin") and actor_id is not None:
        return (actor_type, str(actor_id))
    return ("tenant", str(tenant_id))
