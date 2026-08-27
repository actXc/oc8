"""Capability negotiation (§13, §8.7): core capabilities plus the capabilities
this tenant's ENABLED plugins provide; fail-closed.

The answer comes from the database, not from process memory. There used to be a
per-tenant in-memory registry that `enable_plugin` wrote to and `install_plugin`
read from, and it had one fatal property: it described rows that outlive the
process. After a restart it was empty while the tenant's providers were still
enabled, so a dependent install failed for a provider sitting right there --
curable only by disabling and re-enabling it. Reading the rows removes both the
staleness and the tenant-scoping problem in one go: RLS scopes the query, so one
tenant's provider cannot satisfy another's `depends` even if a caller forgets to
pass a tenant id (the defect class of the hook-registry cross-tenant bug).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

CORE_CAPABILITIES: frozenset[str] = frozenset(
    {"coding-loop", "sandbox", "github-trigger", "human-loop"}
)


def _base_name(cap: str) -> str:
    """`concurrent_tasks:3` -> `concurrent_tasks` for name-level matching."""
    return cap.split(":", 1)[0]


class PluginCapabilityRegistry:
    """A question answered against one fixed set of provided capabilities.

    Immutable on purpose: it is built per check from whatever is enabled right
    now, so there is no long-lived copy that can disagree with the database.
    """

    def __init__(
        self, provided: list[str] | None = None, *, core: frozenset[str] | None = None
    ) -> None:
        self._core = core if core is not None else CORE_CAPABILITIES
        self._provided = {_base_name(n) for n in (provided or [])}

    def has(self, name: str) -> bool:
        base = _base_name(name)
        return base in self._core or base in self._provided

    def missing(self, depends: list[str]) -> list[str]:
        return [d for d in depends if not self.has(d)]

    def supports(self, declared: list[str], required: list[str]) -> list[str]:
        """Return required capabilities NOT covered by `declared` (fail-closed)."""
        declared_bases = {_base_name(c) for c in declared}
        return [r for r in required if _base_name(r) not in declared_bases]


async def provided_capabilities(db: AsyncSession) -> list[str]:
    """Everything this tenant's enabled plugins announce.

    Two sources, because a manifest has two ways to announce one: `capabilities`
    (mirrored into a column) and `provides` (which lives only inside the manifest
    JSON). The in-memory path registered both, so reading only the column would
    quietly narrow what satisfies a dependency.

    RLS scopes this to the calling tenant; there is no tenant_id argument to get
    wrong.
    """
    from oc8.models import CapaInstallation, CapaVersion

    rows = (
        await db.execute(
            select(CapaVersion.capabilities, CapaVersion.manifest)
            .join(CapaInstallation, CapaInstallation.version_id == CapaVersion.id)
            .where(CapaInstallation.status == "enabled")
        )
    ).all()

    out: list[str] = []
    for capabilities, manifest in rows:
        out.extend(str(c) for c in (capabilities or []))
        provides: Any = (manifest or {}).get("provides")
        if isinstance(provides, list):
            out.extend(str(p) for p in provides)
    return out


async def enabled_capability_registry(db: AsyncSession) -> PluginCapabilityRegistry:
    """The registry to answer a `depends` check with, built from the database."""
    return PluginCapabilityRegistry(await provided_capabilities(db))
