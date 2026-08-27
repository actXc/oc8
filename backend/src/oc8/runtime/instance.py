"""Instance context for single-instance Community/Enterprise installations.

This module defines the neutral contract for the one active logical workspace/instance
per Community or self-hosted Enterprise deployment. In SaaS, this is extended by
TenantContext to handle multi-tenancy (see saas/runtime-adapter).

Per spec §3.2 and §6, Community and Enterprise self-hosted are single-instance:
- exactly one active Organization row (root)
- all users and operations bound to that instance
- multi-org detection at startup fails closed with actionable error

This contract is intentionally SaaS-agnostic. It defines only what a Community
deployment needs to know: its one instance ID and basic metadata.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class InstanceContextProtocol(Protocol):
    """Neutral contract for the one active Community/Enterprise instance.

    Implementations are expected to be immutable and cached per request or session.
    """

    @property
    def instance_id(self) -> uuid.UUID:
        """The Organization.id of the singleton root instance."""
        ...

    @property
    def slug(self) -> str:
        """The Organization.slug (unique identifier)."""
        ...

    @property
    def name(self) -> str:
        """The Organization.name (display name)."""
        ...

    @property
    def tier(self) -> str:
        """The Organization.tier (e.g. 'standard', 'enterprise', 'onprem')."""
        ...


@dataclass(frozen=True)
class InstanceContext:
    """Concrete implementation of InstanceContextProtocol.

    Represents a resolved Community/Enterprise singleton instance.
    """

    instance_id: uuid.UUID
    slug: str
    name: str
    tier: str

    @classmethod
    async def resolve_current(
        cls,
        session: AsyncSession,
    ) -> InstanceContext:
        """Resolve the active instance from the database.

        Args:
            session: AsyncSession bound to a migration/admin user (unrestricted by RLS).

        Returns:
            InstanceContext for the singleton root Organization.

        Raises:
            RuntimeError: if zero or multiple Organizations exist (preflight failure).
        """
        from oc8.models import Organization

        result = await session.execute(select(Organization).order_by(Organization.id))
        orgs = result.scalars().all()

        if len(orgs) == 0:
            raise RuntimeError(
                "No Organization found in database. "
                "This is expected only on first install. "
                "Run the setup wizard or initialization script to create the root instance."
            )

        if len(orgs) > 1:
            raise RuntimeError(
                f"Multi-organization configuration detected: {len(orgs)} Organizations exist.\n"
                "Community and self-hosted Enterprise support exactly one root Organization.\n"
                "This usually means:\n"
                "  1. Multiple installations were merged into one database, or\n"
                "  2. A failed migration left stale data.\n"
                "\n"
                "Remediation options:\n"
                "  - Export data from each Organization and merge manually (see docs/MIGRATION.md)\n"
                "  - Delete extra Organizations and their dependent data with administrative tools\n"
                "  - Restore from backup and investigate the root cause\n"
                "\n"
                "Organizations in database:"
                + "\n".join(f"  - {org.id}: {org.slug} ({org.name})" for org in orgs)
            )

        org = orgs[0]
        return cls(
            instance_id=org.id,
            slug=org.slug,
            name=org.name,
            tier=org.tier,
        )


async def check_single_organization_preflight(session: AsyncSession) -> None:
    """Preflight check: ensure exactly one root Organization exists.

    This function is designed to run at app startup or via CLI to catch
    multi-organization configurations before they cause confusing errors.
    It fails closed (raises RuntimeError) if zero or multiple Organizations exist.

    Args:
        session: AsyncSession bound to a migration/admin user (unrestricted by RLS).

    Raises:
        RuntimeError: if not exactly one Organization row exists in the database.
    """
    from oc8.models import Organization

    result = await session.execute(
        select(Organization.__table__.c.id, Organization.__table__.c.slug).order_by(
            Organization.__table__.c.id
        )
    )
    rows = result.all()

    if len(rows) == 0:
        logger.warning(
            "Organization preflight check: database is empty. "
            "This is expected on first startup. No error will be raised."
        )
        return

    if len(rows) > 1:
        slugs = ", ".join(f"'{slug}'" for _, slug in rows)
        raise RuntimeError(
            f"Community/Enterprise single-instance invariant violated: "
            f"{len(rows)} Organizations found ({slugs}). "
            f"Exactly one root Organization is required. "
            f"See MIGRATION.md or contact support for recovery options."
        )

    logger.info(f"Organization preflight check passed: {rows[0][1]}")
