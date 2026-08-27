from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8.auth import get_identity_provider
from oc8.main import create_app

pytestmark = pytest.mark.asyncio


def _token(tenant: uuid.UUID, role: str = "org_admin") -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role=role)


async def test_first_model_created_is_auto_flagged_for_copilot() -> None:
    # A fresh tenant per test, not the shared ACME_TENANT_ID: this test's assertion
    # depends on a tenant-wide row count (auto-flag-the-first-ever-row), which a
    # shared tenant would poison with rows other tests in this session already
    # committed (see docs/memory note on ACME_TENANT_ID test collisions).
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.post(
                "/api/v1/models",
                json={
                    "provider": "anthropic",
                    "model": "claude-3-5-sonnet-20241022",
                    "locality": "cloud",
                },
                headers=headers,
            )
            assert r.status_code == 201, r.text
            assert r.json()["usedByCopilot"] is True


async def test_first_model_created_is_auto_flagged_even_with_explicit_false() -> None:
    # The wizard's ModelPicker UI (a later task) submits an unchecked checkbox as
    # an explicit `false`, not an omitted field. The tenant's first-ever model
    # must still win the flag -- the "only model" invariant overrides whatever
    # the caller explicitly requested, precisely because a tenant must never end
    # up with zero Copilot-eligible models.
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.post(
                "/api/v1/models",
                json={
                    "provider": "anthropic",
                    "model": "claude-3-5-sonnet-20241022",
                    "locality": "cloud",
                    "usedByCopilot": False,
                },
                headers=headers,
            )
            assert r.status_code == 201, r.text
            assert r.json()["usedByCopilot"] is True


async def test_second_model_created_is_not_auto_flagged() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            first = await client.post(
                "/api/v1/models",
                json={
                    "provider": "anthropic",
                    "model": "claude-3-5-sonnet-20241022",
                    "locality": "cloud",
                },
                headers=headers,
            )
            assert first.json()["usedByCopilot"] is True
            second = await client.post(
                "/api/v1/models",
                json={"provider": "openai", "model": "gpt-4o", "locality": "cloud"},
                headers=headers,
            )
            assert second.status_code == 201, second.text
            assert second.json()["usedByCopilot"] is False


async def test_flagging_one_model_for_copilot_clears_the_previous_flag() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            first = await client.post(
                "/api/v1/models",
                json={
                    "provider": "anthropic",
                    "model": "claude-3-5-sonnet-20241022",
                    "locality": "cloud",
                },
                headers=headers,
            )
            second = await client.post(
                "/api/v1/models",
                json={"provider": "openai", "model": "gpt-4o", "locality": "cloud"},
                headers=headers,
            )
            second_id = second.json()["id"]

            updated = await client.patch(
                f"/api/v1/models/{second_id}",
                json={
                    "provider": "openai",
                    "model": "gpt-4o",
                    "locality": "cloud",
                    "usedByCopilot": True,
                },
                headers=headers,
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["usedByCopilot"] is True

            refreshed = await client.get("/api/v1/models", headers=headers)
            first_row = next(mc for mc in refreshed.json() if mc["id"] == first.json()["id"])
            assert first_row["usedByCopilot"] is False
