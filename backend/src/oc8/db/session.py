"""RLS-bound sessions.

Every request runs inside a single transaction whose first statement pins the
tenant via a transaction-local GUC (`set_config(..., is_local=true)`). RLS
policies read `current_setting('app.tenant_id', true)` and fail closed (see no
rows) when it is unset, so a code path that forgets to bind a tenant leaks
nothing -- with one deliberate exception: `organization` (migration 0013)
grants an unbound session read access to every row, specifically so
tenant_session(None) can enumerate all tenants (used by the Trigger Service's
cron scheduler and webhook handler to discover which tenants have due/
matching triggers). Mutations on `organization` still strictly require a
bound tenant_id, so an unbound session can read broadly but never write.
Every other tenant-scoped table remains fully fail-closed when unbound.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from oc8.db.engine import get_sessionmaker


@asynccontextmanager
async def tenant_session(tenant_id: uuid.UUID | None) -> AsyncIterator[AsyncSession]:
    """Open a transaction bound to ``tenant_id`` (or unbound, fail-closed)."""
    sm = get_sessionmaker()
    async with sm() as session:
        if tenant_id is not None:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
