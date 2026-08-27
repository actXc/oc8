"""Tests for dev-only endpoint gating (WP-D).

Verifies that dev endpoints (dev-login, dev-tenants, dev-members) return 404
when is_dev=False, ensuring they are not accessible in Community mode.
"""

from __future__ import annotations

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch

from oc8.main import create_app

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def app_production_mode():
    """Create FastAPI app with is_dev=False (production/Community mode)."""
    with patch("oc8.config.Settings.is_dev", property(lambda self: False)):
        yield create_app()


@pytest.fixture
async def app_dev_mode():
    """Create FastAPI app with is_dev=True (development mode)."""
    with patch("oc8.config.Settings.is_dev", property(lambda self: True)):
        yield create_app()


@pytest.fixture
async def client_production(app_production_mode):
    """AsyncClient for the FastAPI app in production mode."""
    async with LifespanManager(app_production_mode):
        transport = ASGITransport(app=app_production_mode)
        async with AsyncClient(transport=transport, base_url="http://test") as _client:
            yield _client


@pytest.fixture
async def client_dev(app_dev_mode):
    """AsyncClient for the FastAPI app in dev mode."""
    async with LifespanManager(app_dev_mode):
        transport = ASGITransport(app=app_dev_mode)
        async with AsyncClient(transport=transport, base_url="http://test") as _client:
            yield _client


# =============================================================================
# Dev-Only Endpoint Gating Tests
# =============================================================================


async def test_dev_login_returns_404_in_production_mode(client_production: AsyncClient) -> None:
    """POST /auth/dev-login returns 404 when is_dev=False."""
    response = await client_production.post(
        "/api/v1/auth/dev-login",
        json={
            "tenantId": "00000000-0000-0000-0000-000000000001",
            "subject": "test-user",
            "role": "org_admin",
        },
    )
    assert response.status_code == 404


async def test_dev_login_works_in_dev_mode(client_dev: AsyncClient) -> None:
    """POST /auth/dev-login works when is_dev=True."""
    response = await client_dev.post(
        "/api/v1/auth/dev-login",
        json={
            "tenantId": "00000000-0000-0000-0000-000000000001",
            "subject": "test-user",
            "role": "org_admin",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert "principal" in data


async def test_dev_tenants_get_returns_404_in_production_mode(client_production: AsyncClient) -> None:
    """GET /auth/dev-tenants returns 404 when is_dev=False."""
    response = await client_production.get("/api/v1/auth/dev-tenants")
    assert response.status_code == 404


async def test_dev_tenants_get_works_in_dev_mode(client_dev: AsyncClient) -> None:
    """GET /auth/dev-tenants works when is_dev=True."""
    response = await client_dev.get("/api/v1/auth/dev-tenants")
    # May return 200 with empty list or error about missing database,
    # but NOT 404 (which would indicate it's gated)
    assert response.status_code != 404


async def test_dev_tenants_post_returns_404_in_production_mode(client_production: AsyncClient) -> None:
    """POST /auth/dev-tenants returns 404 when is_dev=False."""
    response = await client_production.post(
        "/api/v1/auth/dev-tenants",
        json={
            "name": "Test Company",
            "slug": "test-company",
            "region": "eu",
        },
    )
    assert response.status_code == 404


async def test_dev_tenants_post_works_in_dev_mode(client_dev: AsyncClient) -> None:
    """POST /auth/dev-tenants works when is_dev=True (or fails with 409/500, not 404)."""
    response = await client_dev.post(
        "/api/v1/auth/dev-tenants",
        json={
            "name": "Test Company",
            "slug": "test-company",
            "region": "eu",
        },
    )
    # May return 409 (conflict) or 500 (database error), but NOT 404
    assert response.status_code != 404


async def test_dev_members_returns_404_in_production_mode(client_production: AsyncClient) -> None:
    """GET /auth/dev-members returns 404 when is_dev=False."""
    response = await client_production.get(
        "/api/v1/auth/dev-members",
        params={"tenant_id": "00000000-0000-0000-0000-000000000001"},
    )
    assert response.status_code == 404


async def test_dev_members_works_in_dev_mode(client_dev: AsyncClient) -> None:
    """GET /auth/dev-members works when is_dev=True."""
    response = await client_dev.get(
        "/api/v1/auth/dev-members",
        params={"tenant_id": "00000000-0000-0000-0000-000000000001"},
    )
    # May return 200 with empty list or error about missing database,
    # but NOT 404 (which would indicate it's gated)
    assert response.status_code != 404
