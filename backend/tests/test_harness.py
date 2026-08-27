from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from oc8.db.session import tenant_session
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def _seed_org(
    app_session: AppSessionFactory, org_id: uuid.UUID, slug: str, name: str
) -> None:
    async with app_session(org_id) as s:
        await s.execute(
            text(
                "INSERT INTO organization (id, slug, name, tier, region, settings, "
                "onboarding_status, created_at, updated_at) VALUES (:id, :slug, :name, 'standard', 'eu', "
                "'{}', 'pending', now(), now())"
            ),
            {"id": str(org_id), "slug": slug, "name": name},
        )


async def _org_name(app_session: AppSessionFactory, org_id: uuid.UUID) -> str:
    async with app_session(org_id) as s:
        stmt = text("SELECT name FROM organization WHERE id = :id")
        return (await s.execute(stmt, {"id": str(org_id)})).scalar_one()  # type: ignore[no-any-return]


async def test_rls_isolates_tenants(app_session: AppSessionFactory) -> None:
    """Uses fresh tenants rather than ACME_TENANT_ID/GLOBEX_TENANT_ID for the
    same reason the discovery test below does: organization.id is a singleton PK
    and app_session never rolls back, so any other test in the run that seeds
    under those shared constants collides here. This test passed in isolation
    and failed in the full suite until it stopped sharing them -- which made a
    red suite the normal state and hid real regressions.

    Which two tenants they are does not matter; RLS isolation is the claim."""
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    await _seed_org(app_session, org_a, f"iso-a-{org_a.hex[:12]}", "Acme")
    await _seed_org(app_session, org_b, f"iso-b-{org_b.hex[:12]}", "Globex")

    # A tenant-bound session still only ever sees its own organization row.
    async with app_session(org_a) as s:
        rows = (await s.execute(text("SELECT slug FROM organization"))).scalars().all()
    assert rows == [f"iso-a-{org_a.hex[:12]}"]

    # And cannot mutate another tenant's organization row.
    async with app_session(org_a) as s:
        await s.execute(
            text("UPDATE organization SET name = :name WHERE id = :id"),
            {"name": "Globex Updated", "id": str(org_b)},
        )
    # The UPDATE matched 0 rows (WITH CHECK policy) -- globex's name is unchanged.
    assert await _org_name(app_session, org_b) == "Globex"


async def test_rls_unbound_session_reads_all_organizations_for_discovery(
    app_session: AppSessionFactory,
) -> None:
    """The Trigger Service scheduler/webhook handler enumerate tenants via
    tenant_session(None) -- an unbound session must see every organization
    row, unlike a normal tenant-bound session (see test_rls_isolates_tenants),
    but still cannot write to any of them. Uses its own fresh tenants (not
    ACME_TENANT_ID/GLOBEX_TENANT_ID) since organization.id is a singleton PK
    and app_session never rolls back -- reusing those constants here would
    collide with test_rls_isolates_tenants' own rows in the same test run."""
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    await _seed_org(app_session, org_a, f"disc-a-{org_a.hex[:12]}", "Discovery A")
    await _seed_org(app_session, org_b, f"disc-b-{org_b.hex[:12]}", "Discovery B")

    async with tenant_session(None) as s:
        rows = (
            (
                await s.execute(
                    text("SELECT id FROM organization WHERE id = ANY(:ids)"),
                    {"ids": [org_a, org_b]},
                )
            )
            .scalars()
            .all()
        )
        assert set(rows) == {org_a, org_b}

        await s.execute(
            text("UPDATE organization SET name = :name WHERE id = :id"),
            {"name": "Discovery B Updated", "id": str(org_b)},
        )

    # The UPDATE matched 0 rows (an unbound session can read broadly but
    # never write) -- org_b's name is unchanged.
    assert await _org_name(app_session, org_b) == "Discovery B"
