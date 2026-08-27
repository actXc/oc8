from __future__ import annotations

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from oc8.config import Settings
from oc8.main import create_app

pytestmark = pytest.mark.asyncio


async def _get_config(app: FastAPI) -> dict[str, object]:
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get("/api/v1/auth/config")
            assert r.status_code == 200, r.text
            body = r.json()
            assert isinstance(body, dict)
            return body


async def test_auth_config_dev_mode_by_default() -> None:
    app = create_app()
    body = await _get_config(app)
    assert body["mode"] == "dev"


async def test_auth_config_community_mode_when_not_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A self-hosted deployment with env != "dev" has no dev endpoints
    (`_dev_only()` 404s them) -- it must report "community" so the frontend
    routes to /login (real password auth) instead of a dev persona picker
    that is unreachable there."""
    community = Settings(env="production")
    monkeypatch.setattr("oc8.api.v1.auth.get_settings", lambda: community)
    app = create_app()
    body = await _get_config(app)
    assert body["mode"] == "community"
