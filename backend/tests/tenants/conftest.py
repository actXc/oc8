"""An owner-role session. Provisioning cannot run under the app role: migrations
0013/0015 constrain `organization` INSERT to `id = current_setting('app.tenant_id')`,
so only the schema owner (exempt from RLS, since no table sets FORCE ROW LEVEL
SECURITY) can create a tenant."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest
from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from oc8.config import get_settings

OwnerSessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@pytest.fixture
def owner_session() -> OwnerSessionFactory:
    @asynccontextmanager
    async def _open() -> AsyncIterator[AsyncSession]:
        engine = create_async_engine(get_settings().migration_async_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as s:
                try:
                    yield s
                    await s.commit()
                except Exception:
                    await s.rollback()
                    raise
        finally:
            await engine.dispose()

    return _open
