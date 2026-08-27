from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8.events.dispatcher import get_dispatcher
from oc8.events.types import InboundEvent
from oc8.main import create_app

pytestmark = pytest.mark.asyncio


async def test_signed_github_event_dispatched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OC8_GITHUB_WEBHOOK_SECRET", "hooksecret")
    from oc8.config import get_settings

    get_settings.cache_clear()

    seen: list[InboundEvent] = []
    get_dispatcher().register("github", "github.issues.opened", lambda e: _record(seen, e))

    body = json.dumps({"action": "opened", "issue": {"number": 7}}).encode()
    sig = "sha256=" + hmac.new(b"hooksecret", body, hashlib.sha256).hexdigest()

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                "/api/v1/events/github",
                content=body,
                headers={
                    "X-GitHub-Event": "issues",
                    "X-Hub-Signature-256": sig,
                    "content-type": "application/json",
                },
            )
            assert r.status_code == 202
            assert r.json()["dispatched"] == 2
            assert seen and seen[0].type == "github.issues.opened"

            bad = await client.post(
                "/api/v1/events/github",
                content=body,
                headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": "sha256=deadbeef"},
            )
            assert bad.status_code == 401


async def _record(seen: list[InboundEvent], e: InboundEvent) -> None:
    seen.append(e)
