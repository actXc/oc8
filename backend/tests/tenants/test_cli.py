from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

from oc8.tenants import cli as tcli
from oc8.tenants.provision import create_tenant
from tests.tenants.conftest import OwnerSessionFactory

pytestmark = pytest.mark.asyncio


def _slug() -> str:
    return f"muster-{uuid.uuid4().hex[:10]}"


@pytest.fixture(autouse=True)
async def _cleanup_organization(owner_session: OwnerSessionFactory) -> AsyncIterator[None]:
    """This file inserts real Organization rows (random slug per test, via
    `_slug()`) that nothing here ever deletes -- pre-dates the Community
    single-instance invariant other test files now assume. A leftover row is
    invisible to an app-role session scoped to a different tenant (RLS), so it
    silently breaks any later file's singleton-org check (e.g.
    tests/auth/test_password_auth.py's org_in_db, 409 'Multi-organization
    configuration'). No test in this file depends on another's leftover org
    (each uses its own random slug), so per-test cleanup is safe here.
    """
    yield
    async with owner_session() as session:
        await session.execute(text("DELETE FROM organization"))


async def test_list_prints_created_tenants(
    owner_session: OwnerSessionFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    # NOTE: the test fixtures only run migrations — they never load seed data,
    # so there is no pre-existing "acme"/"globex" to assert on. Create our own.
    slug = _slug()
    async with owner_session() as db:
        created = await create_tenant(db, slug=slug, name="Listed GmbH")

    assert await tcli.cmd_list() == 0
    out = capsys.readouterr().out
    assert slug in out
    assert str(created.tenant_id) in out
    assert "Listed GmbH" in out
