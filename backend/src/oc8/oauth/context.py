"""The `AuthContext` a connector receives: ask for a token, get a fresh one.

Connectors never see the connection row, the secret store, or an expiry --
refresh is entirely hidden behind `token()`.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from oc8.oauth.tokens import get_access_token


class ConnectionAuthContext:
    def __init__(
        self, db: AsyncSession, *, tenant_id: uuid.UUID, connection_id: uuid.UUID
    ) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._connection_id = connection_id

    async def token(self) -> str:
        return await get_access_token(
            self._db, tenant_id=self._tenant_id, connection_id=self._connection_id
        )
