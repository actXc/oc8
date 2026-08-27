"""Regression test for migration 0015: organization's SELECT RLS policy must
survive a pooled connection that was previously tenant-bound.

Uses `oc8.db.session.tenant_session` directly against `oc8.db.engine`'s
shared, connection-pooled engine -- the same engine the Trigger Service's
scheduler and webhook handler loop against forever (see
`tests/triggers/test_scheduler.py` / `test_handler.py`, which exercise the
same bug indirectly through `run_scheduler_tick`). This test pins the bug at
the RLS-policy level, independent of the scheduler.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.db.session import tenant_session

pytestmark = pytest.mark.asyncio


async def test_unbound_discovery_read_survives_prior_bound_use_on_pooled_connection() -> None:
    """`set_config('app.tenant_id', <uuid>, is_local => true)` only resets to
    NULL the FIRST time a physical connection ever touches this custom GUC.
    Once any transaction on that connection has bound a real tenant_id and
    committed, the GUC's post-transaction reset value becomes '' (empty
    string), not NULL, for the rest of that connection's life. Pre-migration
    0015, organization's SELECT policy only checked
    `current_setting(...) IS NULL`, so a later UNBOUND discovery read on that
    same (now-primed) pooled connection fell through to
    `id = current_setting(...)::uuid` and raised
    `invalid input syntax for type uuid: ""` instead of matching every row.

    This exact sequence -- bind a real tenant (commit), then read unbound on
    the same connection -- reproduces the crash pre-0015 and must pass clean
    post-0015."""
    tenant = uuid.uuid4()

    # Bind a real tenant_id and commit: this is what primes a pooled
    # connection's custom GUC reset value to '' instead of NULL.
    async with tenant_session(tenant) as db:
        db.add(
            m.Organization(
                id=tenant,
                slug=f"pooled-rls-{tenant.hex[:12]}",
                name="Pooled RLS Test",
                tier="standard",
                region="eu",
            )
        )
        await db.flush()

    # Unbound discovery read on (almost certainly) the same physical
    # connection -- this is list_active_tenant_ids()'s exact access pattern.
    # Must see the row just inserted above, not raise.
    async with tenant_session(None) as db:
        ids = (await db.execute(select(m.Organization.id))).scalars().all()

    assert tenant in ids
