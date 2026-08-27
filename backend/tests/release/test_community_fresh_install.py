"""Release proof for the Community self-hosted first-run path.

This is deliberately an ASGI/API test: the frontend has no browser-test runner
or browser dependency, while this exercises the same unauthenticated endpoints
the setup/login screen uses against migrated PostgreSQL with RLS enabled.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from oc8.config import Settings, get_settings
from oc8.main import create_app

pytestmark = pytest.mark.asyncio


async def _empty_community_database() -> None:
    """Remove the singleton root and its cascaded Community data for this proof."""
    engine = create_async_engine(get_settings().migration_async_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(text("DELETE FROM org_member"))
            await db.execute(text("DELETE FROM organization"))
            await db.commit()
    finally:
        await engine.dispose()


async def test_fresh_community_install_bootstraps_and_reauthenticates_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean instance uses setup/login only; no browser-selected tenant exists."""
    current = get_settings()
    community = Settings(
        env="prod",
        database_url=current.database_url,
        migration_url=current.migration_url,
        redis_url=current.redis_url,
        jwt_secret=current.jwt_secret,
    )
    await _empty_community_database()

    # Keep this narrow patch local to auth: DB sessions still use the migrated
    # testcontainer URLs above, but auth/config and dev-only guards see Community mode.
    with patch("oc8.api.v1.auth.get_settings", return_value=community):
        app = create_app()
        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                config = await client.get("/api/v1/auth/config")
                assert config.status_code == 200
                assert config.json() == {
                    "mode": "community",
                    "authServerUrl": "",
                    "realm": "",
                    "clientId": "",
                    "initialized": False,
                }

                # Dev helpers stay unavailable; neither request body below has
                # a tenant/organization selector.
                assert (await client.post("/api/v1/auth/dev-login", json={})).status_code == 404

                setup = await client.post(
                    "/api/v1/auth/setup",
                    json={
                        "email": "admin@example.com",
                        "password": "SecurePassword123!",
                        "displayName": "Community Admin",
                    },
                )
                assert setup.status_code == 201, setup.text
                first_session = setup.json()
                assert first_session["principal"]["subject"] == "admin@example.com"
                assert first_session["principal"]["role"] == "org_admin"

                admin_headers = {"Authorization": f"Bearer {first_session['token']}"}
                me = await client.get("/api/v1/me", headers=admin_headers)
                assert me.status_code == 200, me.text
                assert me.json()["subject"] == "admin@example.com"
                assert me.json()["tenantId"] == first_session["principal"]["tenant_id"]

                # The stable member-management API is instance-scoped through
                # the admin token, not a tenant field supplied by the browser.
                member = await client.post(
                    "/api/v1/members",
                    json={"subject": "teammate@example.com", "displayName": "Teammate"},
                    headers=admin_headers,
                )
                assert member.status_code == 201, member.text
                assert member.json()["subject"] == "teammate@example.com"

                logout = await client.post("/api/v1/auth/logout", headers=admin_headers)
                assert logout.status_code == 204
                assert (await client.get("/api/v1/me")).status_code == 401

                login = await client.post(
                    "/api/v1/auth/login",
                    json={"email": "admin@example.com", "password": "SecurePassword123!"},
                )
                assert login.status_code == 200, login.text
                assert (
                    login.json()["principal"]["tenant_id"]
                    == first_session["principal"]["tenant_id"]
                )
