"""C2: concurrent appends for one tenant must not fork the hash chain.

append_event reads the tenant's chain head and then awaits before flushing the
new row. Without serialisation two concurrent appends both read head H and both
write prev_hash = H; the second row's prev_hash then does not match its
predecessor's hash and the verifier reports "broken" -- a false tamper alarm
that can never be cleared, because audit_event is append-only (migration 0001
REVOKEs UPDATE/DELETE from oc8_app) so the forked rows cannot be repaired.

Each append runs in its OWN session/transaction: a single shared session would
serialise trivially on the connection and prove nothing about the lock.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from tests.conftest import AppSessionFactory

from oc8.audit.chain import append_event, verify_chain

pytestmark = pytest.mark.asyncio

CONCURRENCY = 8


async def test_concurrent_appends_do_not_fork_the_chain(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()

    async def _append(i: int) -> None:
        async with app_session(tenant) as s:
            await append_event(
                s,
                tenant_id=tenant,
                actor_type="system",
                actor_id=None,
                category="test",
                action=f"concurrent.{i}",
            )

    await asyncio.gather(*(_append(i) for i in range(CONCURRENCY)))

    async with app_session(tenant) as s:
        count = (
            await s.execute(
                sa.text("SELECT count(*) FROM audit_event WHERE tenant_id = :t"),
                {"t": str(tenant)},
            )
        ).scalar_one()
        assert count == CONCURRENCY
        assert await verify_chain(s, tenant) is True
