"""Tests for InstanceContext contract and single-organization preflight check."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

from oc8.models import Organization
from oc8.runtime.instance import (
    InstanceContext,
    check_single_organization_preflight,
)
from tests.tenants.conftest import OwnerSessionFactory

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _cleanup_organization(owner_session: OwnerSessionFactory) -> AsyncIterator[None]:
    """Every test below creates Organization row(s) via the RLS-exempt owner
    session and only cleans up BEFORE itself -- harmless within this file
    (each test starts by deleting), but the last test's rows leak into
    later files. Those scope Organization by a fixed tenant id and, under
    RLS, can't even see rows outside it to delete them (e.g.
    tests/auth/test_password_auth.py's org_in_db), so a leaked row here
    produces a spurious multi-organization 409 there. Clean up after every
    test here too.
    """
    yield
    async with owner_session() as session:
        await session.execute(text("DELETE FROM organization"))


async def test_instance_context_resolve_single_org(
    owner_session: OwnerSessionFactory,
) -> None:
    """Happy path: exactly one Organization exists and resolves correctly."""
    async with owner_session() as session:
        # Ensure clean state.
        await session.execute(text("DELETE FROM organization"))

        # Create one organization.
        org_id = uuid.uuid4()
        org = Organization(
            id=org_id,
            slug="test-org",
            name="Test Organization",
            tier="standard",
        )
        session.add(org)
        await session.commit()

        ctx = await InstanceContext.resolve_current(session)

        assert ctx.instance_id == org_id
        assert ctx.slug == "test-org"
        assert ctx.name == "Test Organization"
        assert ctx.tier == "standard"


async def test_instance_context_fails_no_org(
    owner_session: OwnerSessionFactory,
) -> None:
    """Failure path: no Organizations exist."""
    async with owner_session() as session:
        # Ensure clean state.
        await session.execute(text("DELETE FROM organization"))
        await session.commit()

        with pytest.raises(RuntimeError, match="No Organization found"):
            await InstanceContext.resolve_current(session)


async def test_instance_context_fails_multiple_orgs(
    owner_session: OwnerSessionFactory,
) -> None:
    """Failure path: multiple Organizations exist (single-instance invariant violation)."""
    async with owner_session() as session:
        # Ensure clean state.
        await session.execute(text("DELETE FROM organization"))

        # Create two organizations to violate the invariant.
        org1_id = uuid.uuid4()
        org1 = Organization(
            id=org1_id,
            slug="org-1",
            name="Organization 1",
            tier="standard",
        )
        org2_id = uuid.uuid4()
        org2 = Organization(
            id=org2_id,
            slug="org-2",
            name="Organization 2",
            tier="standard",
        )
        session.add_all([org1, org2])
        await session.commit()

        with pytest.raises(RuntimeError, match="Multi-organization configuration"):
            await InstanceContext.resolve_current(session)


async def test_preflight_check_pass_single_org(
    owner_session: OwnerSessionFactory,
) -> None:
    """Preflight check passes when exactly one Organization exists."""
    async with owner_session() as session:
        # Ensure clean state.
        await session.execute(text("DELETE FROM organization"))

        # Create one organization.
        org = Organization(
            id=uuid.uuid4(),
            slug="test-org",
            name="Test Organization",
            tier="standard",
        )
        session.add(org)
        await session.commit()

        # This should not raise.
        await check_single_organization_preflight(session)


async def test_preflight_check_empty_db(
    owner_session: OwnerSessionFactory,
) -> None:
    """Preflight check logs warning but does not raise when database is empty."""
    async with owner_session() as session:
        # Delete all organizations.
        await session.execute(text("DELETE FROM organization"))
        await session.commit()

        # Should not raise, only log a warning.
        await check_single_organization_preflight(session)


async def test_preflight_check_fails_multiple_orgs(
    owner_session: OwnerSessionFactory,
) -> None:
    """Preflight check fails when multiple Organizations exist."""
    async with owner_session() as session:
        # Ensure clean state.
        await session.execute(text("DELETE FROM organization"))

        # Create two organizations.
        org1 = Organization(
            id=uuid.uuid4(),
            slug="org-1",
            name="Organization 1",
            tier="standard",
        )
        org2 = Organization(
            id=uuid.uuid4(),
            slug="org-2",
            name="Organization 2",
            tier="standard",
        )
        session.add_all([org1, org2])
        await session.commit()

        with pytest.raises(RuntimeError, match="Community/Enterprise single-instance invariant"):
            await check_single_organization_preflight(session)
