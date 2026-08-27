"""GET /channels -- which channel ids a tenant can actually link to.

Reuses the same `channels_for_tenant` discovery `create_link_code` and the
inbound webhook already trust, so this is a thin proof that the route wires
that discovery through honestly rather than hardcoding channel ids.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8.auth import get_identity_provider
from oc8.main import create_app

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def test_lists_the_channels_the_registry_discovers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _channels(_db: Any, *, tenant_id: uuid.UUID) -> dict[str, Any]:
        return {"whatsapp": object(), "telegram": object()}

    monkeypatch.setattr("oc8.api.v1.channels.channels_for_tenant", _channels)

    tenant = uuid.uuid4()
    token = get_identity_provider().mint(tenant_id=tenant, subject="viewer", role="org_admin")
    async with _http() as http:
        r = await http.get("/api/v1/channels", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200, r.text
    # Sorted: the frontend renders these in a stable, predictable order rather
    # than whatever dict-iteration order the registry happened to build.
    assert r.json() == [{"id": "telegram"}, {"id": "whatsapp"}]


async def test_empty_when_nothing_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _channels(_db: Any, *, tenant_id: uuid.UUID) -> dict[str, Any]:
        return {}

    monkeypatch.setattr("oc8.api.v1.channels.channels_for_tenant", _channels)

    tenant = uuid.uuid4()
    token = get_identity_provider().mint(tenant_id=tenant, subject="viewer", role="org_admin")
    async with _http() as http:
        r = await http.get("/api/v1/channels", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200, r.text
    assert r.json() == []
