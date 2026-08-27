"""`oc8 seed --reset` may not destroy authorization a customer typed.

`--reset` TRUNCATEs every table in `Base.metadata`. Until this slice everything
in there was either demo data this file writes or state a running system
rebuilds, so the flag was a developer convenience with no way to lose anything.
`role_permission` and `org_member.role_id` change that: they are the tenant's own
configuration, there is no backup of them anywhere in this repository, and
`docker-compose.yml` runs `oc8 seed` on start whenever `OC8_SEED_ON_START=true`.

The failure it would produce is also the quietest one available. Nothing errors,
no screen shows a gap, and the only visible symptom is that everybody who had
been narrowed is an `org_admin` again -- which is what a working system looked
like the day before.

Asserted against the guard rather than by running `run_seed(reset=True)`: the
TRUNCATE would empty the shared test database out from under every other test in
the session, and the branch under test is precisely the one that decides whether
the TRUNCATE happens at all.

Everything below is a DELTA rather than an absolute. The count is global,
because `--reset` is, and this database is shared by the whole test session with
no per-test rollback -- so "the count is zero" is a statement about whatever else
ran first, and "adding a built-in row did not change the count" is a statement
about the guard.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from oc8 import models as m
from oc8.authz.permissions import APPROVAL, VIEW, perm
from oc8.seed import (
    TenantAuthorizationWouldBeLost,
    _refuse_reset_over_tenant_roles,
    tenant_defined_role_count,
)
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_a_reset_over_a_tenant_defined_role_is_refused(
    app_session: AppSessionFactory,
) -> None:
    """One role a tenant composed is enough, and the refusal says what it is
    about rather than that something went wrong."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        before = await tenant_defined_role_count(db)
        role = m.Role(tenant_id=tenant, name=f"Freigabe {uuid.uuid4().hex[:8]}", kind="human")
        db.add(role)
        await db.flush()
        db.add(m.RolePermission(tenant_id=tenant, role_id=role.id, permission=perm(APPROVAL, VIEW)))
        await db.flush()

        assert await tenant_defined_role_count(db) == before + 1, (
            "the guard does not see a role a tenant just composed"
        )
        with pytest.raises(TenantAuthorizationWouldBeLost) as refused:
            await _refuse_reset_over_tenant_roles(db)

    assert "--reset" in str(refused.value)
    assert "role" in str(refused.value)


async def test_a_builtin_row_and_a_tombstone_are_not_counted(
    app_session: AppSessionFactory,
) -> None:
    """Neither of the two rows that look like tenant configuration and are not.

    A BUILT-IN row is written by `create_tenant` and by the seed itself, and
    re-seeding reproduces it exactly. A SOFT-DELETED tenant role is a tombstone
    holding a name so that the name never comes back meaning something new;
    nothing resolves through it. Counting either would make `--reset` refuse on
    every database that has ever had a tenant, i.e. always, and a guard that
    always fires is a guard everybody learns to work around.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        before = await tenant_defined_role_count(db)

        db.add(m.Role(tenant_id=tenant, name=f"operator-{uuid.uuid4().hex[:8]}", builtin=True))
        gone = m.Role(tenant_id=tenant, name=f"Alt {uuid.uuid4().hex[:8]}", kind="human")
        db.add(gone)
        await db.flush()
        gone.deleted_at = dt.datetime.now(tz=dt.UTC)
        await db.flush()

        assert await tenant_defined_role_count(db) == before, (
            "a built-in row or a tombstone made --reset refuse; the flag now "
            "refuses on every database that has ever had a tenant"
        )
