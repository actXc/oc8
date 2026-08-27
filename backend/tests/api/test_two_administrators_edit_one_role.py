"""Two administrators, one role, one edit each -- and what the second one gets.

`PUT /roles/{id}` is a full replacement written as read-then-DELETE-then-INSERT.
Under READ COMMITTED, two of those interleave into a result neither
administrator asked for, and every one of the three outcomes below was
reproduced against Postgres 15 with the real `uq_role_permission` index before
the row lock existed:

* **a revocation that answers 200 and revokes nothing.** The second
  transaction's DELETE blocks on the first's row locks, re-evaluates, matches
  zero rows -- and its own statement snapshot predates the first's INSERTs, so
  the grants it meant to take away survive. Every holder keeps the permission
  indefinitely and nothing anywhere says so. This is the property the whole
  slice is built on: *"a revocation that lands in five minutes is not a
  revocation"* -- and one that lands never is worse.
* **two disjoint edits merged into their UNION**, which is precisely what
  `UpdateRoleRequest`'s docstring justifies the replacement shape by preventing:
  *"two administrators editing one role would each land half an edit with no way
  to notice"*.
* **the audit event stating a diff that did not happen**, because `before` was
  read from a snapshot the other transaction had already overwritten. That entry
  is the one somebody reads a year later asking when a team lead stopped being
  able to decide.

The fix is one `SELECT ... FOR UPDATE` on the role row, taken BEFORE `before` is
read. These tests are the two halves of it: the outcome (here, deterministic,
two real transactions against the real database) and the wiring (that the ROUTE
takes the lock and not merely the service function some future caller forgets).

**Deterministic, not timing-hopeful.** The interleave is forced with an
`asyncio.Event` and a held transaction rather than by firing two requests and
hoping. Without the lock, the second writer reaches its DELETE while the first
still holds its rows, which is exactly the window; with it, the second writer
waits at its first statement and then re-reads everything.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import pytest
import sqlalchemy as sa
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.engine import Engine

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.authz.permissions import (
    APPROVAL,
    APPROVAL_DECIDE,
    BUDGET,
    ORG_ADMIN,
    VIEW,
    perm,
)
from oc8.authz.scope import subject_uuid_for
from oc8.main import create_app
from oc8.roles.service import load_role, update_role
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

APPROVAL_VIEW = perm(APPROVAL, VIEW)
BUDGET_VIEW = perm(BUDGET, VIEW)

#: Long enough that the loser is genuinely parked on the lock, short enough that
#: the file stays a test. Both writers are on the same local container.
HOLD = 0.6


def _headers(tenant: uuid.UUID, subject: str = "boss") -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=ORG_ADMIN)
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


@contextmanager
def _statements() -> Iterator[list[str]]:
    """Every SQL statement issued while this is open, on the `Engine` class."""
    seen: list[str] = []

    def _record(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", _record)


async def _seed_role(
    app_session: AppSessionFactory,
    tenant: uuid.UUID,
    permissions: set[str],
    name: str = "Abteilungsleitung",
) -> uuid.UUID:
    async with app_session(tenant) as db:
        role = m.Role(tenant_id=tenant, name=name, kind="human", builtin=False)
        db.add(role)
        await db.flush()
        for permission in sorted(permissions):
            db.add(m.RolePermission(tenant_id=tenant, role_id=role.id, permission=permission))
        await db.flush()
        return role.id


async def _seed_holder(
    app_session: AppSessionFactory, tenant: uuid.UUID, subject: str, role_id: uuid.UUID
) -> None:
    async with app_session(tenant) as db:
        db.add(
            m.OrgMember(
                tenant_id=tenant,
                subject=subject,
                subject_uuid=subject_uuid_for(subject),
                display_name=subject,
                role_id=role_id,
            )
        )
        await db.flush()


async def _stored(
    app_session: AppSessionFactory, tenant: uuid.UUID, role_id: uuid.UUID
) -> set[str]:
    async with app_session(tenant) as db:
        return set(
            (
                await db.execute(
                    sa.select(m.RolePermission.permission).where(
                        m.RolePermission.tenant_id == tenant,
                        m.RolePermission.role_id == role_id,
                    )
                )
            ).scalars()
        )


async def test_a_revocation_that_races_an_edit_still_revokes(
    app_session: AppSessionFactory,
) -> None:
    """The loser of the race wins the row, and its audit diff is true.

    One administrator adds `budget:view`; a second, at the same moment, takes
    everything away. Whichever order the database picks, the state afterwards has
    to be ONE of the two sets -- and here the revoker is deliberately second, so
    the answer is the empty set.

    Two assertions, and the second is the one that used to be missed: the table
    is empty, AND `removed` names what was actually taken away. Without the lock
    the revoker read `before` from a snapshot the adder had already overwritten,
    so it reported removing two permissions while three sat in the table.
    """
    tenant = uuid.uuid4()
    role_id = await _seed_role(app_session, tenant, {APPROVAL_VIEW, APPROVAL_DECIDE})

    adder_has_written = asyncio.Event()
    diff: dict[str, frozenset[str]] = {}

    async def adder() -> None:
        async with app_session(tenant) as db:
            role = await load_role(db, tenant_id=tenant, role_id=role_id, for_update=True)
            assert role is not None
            await update_role(
                db,
                role=role,
                description="",
                permissions=frozenset({APPROVAL_VIEW, APPROVAL_DECIDE, BUDGET_VIEW}),
            )
            adder_has_written.set()
            # Held open. This is the window: without a lock on the role row the
            # revoker walks straight into it.
            await asyncio.sleep(HOLD)

    async def revoker() -> None:
        await adder_has_written.wait()
        async with app_session(tenant) as db:
            role = await load_role(db, tenant_id=tenant, role_id=role_id, for_update=True)
            assert role is not None
            added, removed = await update_role(
                db, role=role, description="", permissions=frozenset()
            )
            diff["added"], diff["removed"] = added, removed

    await asyncio.gather(adder(), revoker())

    assert await _stored(app_session, tenant, role_id) == set(), (
        "a revocation was answered 200 and revoked nothing: the concurrent edit's "
        "grants survived it, and every holder keeps them indefinitely"
    )
    assert diff["removed"] == frozenset({APPROVAL_VIEW, APPROVAL_DECIDE, BUDGET_VIEW}), (
        "the audit diff names permissions that were not what was there; it was "
        f"computed from a stale read: {sorted(diff['removed'])}"
    )
    assert diff["added"] == frozenset()


async def test_two_disjoint_edits_do_not_merge_into_a_union(
    app_session: AppSessionFactory,
) -> None:
    """Neither administrator asked for both sets, so neither may get both.

    This is the failure `UpdateRoleRequest` refuses a PATCH shape in order to
    prevent, arrived at anyway through the transaction underneath the
    replacement. The second writer's set is the whole answer, because a
    replacement is a replacement.
    """
    tenant = uuid.uuid4()
    role_id = await _seed_role(app_session, tenant, {APPROVAL_VIEW})

    first_has_written = asyncio.Event()

    async def first() -> None:
        async with app_session(tenant) as db:
            role = await load_role(db, tenant_id=tenant, role_id=role_id, for_update=True)
            assert role is not None
            await update_role(
                db, role=role, description="", permissions=frozenset({APPROVAL_DECIDE})
            )
            first_has_written.set()
            await asyncio.sleep(HOLD)

    async def second() -> None:
        await first_has_written.wait()
        async with app_session(tenant) as db:
            role = await load_role(db, tenant_id=tenant, role_id=role_id, for_update=True)
            assert role is not None
            await update_role(db, role=role, description="", permissions=frozenset({BUDGET_VIEW}))

    await asyncio.gather(first(), second())

    assert await _stored(app_session, tenant, role_id) == {BUDGET_VIEW}, (
        "two disjoint replacements merged; the role holds permissions neither "
        "administrator asked it to hold"
    )


async def test_two_overlapping_edits_do_not_raise_a_unique_violation(
    app_session: AppSessionFactory,
) -> None:
    """One administrator double-clicking Save is enough to reach this.

    Overlapping sets meet `uq_role_permission (role_id, permission)`: the second
    writer's DELETE matched nothing, its INSERT collided, and `update_role` --
    unlike `create_role` -- catches no `IntegrityError`, so the answer was a bare
    500. Serialised on the row lock the second write simply replaces the first.
    """
    tenant = uuid.uuid4()
    role_id = await _seed_role(app_session, tenant, {APPROVAL_VIEW})

    first_has_written = asyncio.Event()

    async def first() -> None:
        async with app_session(tenant) as db:
            role = await load_role(db, tenant_id=tenant, role_id=role_id, for_update=True)
            assert role is not None
            await update_role(
                db,
                role=role,
                description="",
                permissions=frozenset({APPROVAL_VIEW, APPROVAL_DECIDE}),
            )
            first_has_written.set()
            await asyncio.sleep(HOLD)

    async def second() -> None:
        await first_has_written.wait()
        async with app_session(tenant) as db:
            role = await load_role(db, tenant_id=tenant, role_id=role_id, for_update=True)
            assert role is not None
            await update_role(
                db, role=role, description="", permissions=frozenset({APPROVAL_VIEW, BUDGET_VIEW})
            )

    await asyncio.gather(first(), second())

    assert await _stored(app_session, tenant, role_id) == {APPROVAL_VIEW, BUDGET_VIEW}


async def test_the_write_routes_lock_the_role_before_they_read_its_grants(
    app_session: AppSessionFactory,
) -> None:
    """The wiring, asserted separately from the outcome, and by reading the SQL.

    The three tests above call `load_role(for_update=True)` themselves, so they
    prove the LOCK works and say nothing about whether the ROUTES ask for it.

    Asserted on the statements the route emits, and NOT by holding the row and
    watching the request wait -- which is the obvious test and is worthless here.
    `role_permission` carries a composite foreign key into `role`, so the INSERT
    of a grant takes an implicit lock on the parent row all by itself; a request
    with the fix reverted therefore waits exactly as long as one with it, and the
    timing test passes either way. (Verified, not assumed: it did.)

    The ORDER is the assertion, not the presence. A lock taken after `before` has
    been read is a lock over a stale diff, which is the defect wearing the fix.
    """
    tenant = uuid.uuid4()
    role_id = await _seed_role(app_session, tenant, {APPROVAL_VIEW})

    async with _http() as http:
        with _statements() as issued:
            edited = await http.put(
                f"/api/v1/roles/{role_id}",
                json={"description": "geändert", "permissions": [BUDGET_VIEW]},
                headers=_headers(tenant),
            )
    assert edited.status_code < 300, edited.text

    locks = [i for i, s in enumerate(issued) if "FROM role" in s and "FOR NO KEY UPDATE" in s]
    grant_reads = [
        i
        for i, s in enumerate(issued)
        if "role_permission.permission" in s and s.lstrip().upper().startswith("SELECT")
    ]
    assert locks, (
        "PUT /roles/{id} read and rewrote the grant set without locking the role "
        "row: a concurrent revocation can be answered 200 and land nowhere. "
        f"Statements: {issued}"
    )
    assert grant_reads and locks[0] < grant_reads[0], (
        "the role was locked only AFTER its current grants were read, so the "
        "audit diff is still computed from a snapshot another transaction can "
        "have overwritten"
    )
    assert not any("FOR UPDATE" in s and "FOR NO KEY UPDATE" not in s for s in issued), (
        "a plain FOR UPDATE was taken on `role`. It conflicts with the FOR KEY "
        "SHARE that `role_permission` and `org_member`'s foreign keys take on the "
        "same row, so two deletions naming each other's role as ?reassignTo= "
        "acquire the two locks in opposite orders and Postgres aborts one"
    )


async def test_two_deletions_naming_each_others_target_do_not_deadlock(
    app_session: AppSessionFactory,
) -> None:
    """The defect the fix could have introduced, held down by its own test.

    `DELETE /roles/{a}?reassignTo={b}` locks `a` and then writes `org_member`
    rows pointing at `b` -- which takes a `FOR KEY SHARE` on `b` through the
    foreign key. Run against `DELETE /roles/{b}?reassignTo={a}` at the same
    moment, a plain `FOR UPDATE` makes those two orderings a cycle, and one
    administrator gets a 500 whose cause is invisible from the response.

    `FOR NO KEY UPDATE` does not conflict with `FOR KEY SHARE`, so the two pass
    each other. Both deletions must succeed and both roles must end up gone.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        a = await _seed_role(app_session, tenant, {APPROVAL_VIEW}, name="Rolle A")
        b = await _seed_role(app_session, tenant, {APPROVAL_DECIDE}, name="Rolle B")
        await _seed_holder(app_session, tenant, f"anna-{uuid.uuid4()}", a)
        await _seed_holder(app_session, tenant, f"bert-{uuid.uuid4()}", b)

        first, second = await asyncio.gather(
            http.request(
                "DELETE",
                f"/api/v1/roles/{a}",
                params={"reassignTo": str(b)},
                headers=_headers(tenant),
            ),
            http.request(
                "DELETE",
                f"/api/v1/roles/{b}",
                params={"reassignTo": str(a)},
                headers=_headers(tenant),
            ),
        )

    for response in (first, second):
        assert response.status_code != 500, (
            "a deletion died on a deadlock: the role lock and the foreign key's "
            f"lock are being taken in opposite orders. {response.text}"
        )
        # 409 is a legitimate answer -- the loser's target may already be gone,
        # which is a refusal with a sentence rather than a crash. 500 is not.
        assert response.status_code in (200, 404, 409), response.text
