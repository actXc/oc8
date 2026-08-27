from __future__ import annotations

import base64
import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from oc8.secrets.service import resolve_secret, store_secret
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    # get_key_provider reads settings.secret_kek via get_settings(); set it so
    # the store is available (mirrors tests/secrets/test_service.py).
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )


def _token(tenant: uuid.UUID, role: str = "org_admin") -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="op", role=role)


def _headers(tenant: uuid.UUID, role: str = "org_admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(tenant, role)}"}


async def test_create_list_delete_secret_roundtrip(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            create_r = await client.post(
                "/api/v1/secrets",
                json={"name": "gh", "value": "ghp_secret", "kind": "api_key"},
                headers=_headers(tenant),
            )
            assert create_r.status_code == 201, create_r.text
            body = create_r.json()
            assert "value" not in body
            assert body["name"] == "gh"
            assert body["kind"] == "api_key"
            assert body["keyVersion"]
            assert body["createdAt"]
            secret_id = body["id"]

            list_r = await client.get("/api/v1/secrets", headers=_headers(tenant))
            assert list_r.status_code == 200, list_r.text
            listed = list_r.json()
            assert len(listed) == 1
            assert "value" not in listed[0]
            assert listed[0]["id"] == secret_id
            assert listed[0]["name"] == "gh"

            del_r = await client.delete(f"/api/v1/secrets/{secret_id}", headers=_headers(tenant))
            assert del_r.status_code == 204, del_r.text

            list_r2 = await client.get("/api/v1/secrets", headers=_headers(tenant))
            assert list_r2.status_code == 200, list_r2.text
            assert list_r2.json() == []


async def test_create_secret_forbidden_for_non_admin() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post(
                "/api/v1/secrets",
                json={"name": "gh", "value": "ghp_secret"},
                headers=_headers(tenant, role="member"),
            )
            assert r.status_code == 403, r.text


async def test_list_secrets_forbidden_for_non_admin() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.get(
                "/api/v1/secrets",
                headers=_headers(tenant, role="member"),
            )
            assert r.status_code == 403, r.text


async def test_delete_secret_forbidden_for_non_admin() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.delete(
                f"/api/v1/secrets/{uuid.uuid4()}",
                headers=_headers(tenant, role="member"),
            )
            assert r.status_code == 403, r.text


async def test_delete_secret_cross_tenant_404(app_session: AppSessionFactory) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            create_r = await client.post(
                "/api/v1/secrets",
                json={"name": "gh", "value": "ghp_secret"},
                headers=_headers(tenant_a),
            )
            assert create_r.status_code == 201, create_r.text
            secret_id = create_r.json()["id"]

            del_r = await client.delete(
                f"/api/v1/secrets/{secret_id}", headers=_headers(tenant_b)
            )
            assert del_r.status_code == 404, del_r.text


async def test_delete_missing_secret_404() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.delete(
                f"/api/v1/secrets/{uuid.uuid4()}", headers=_headers(tenant)
            )
            assert r.status_code == 404, r.text


async def test_create_secret_without_kek_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oc8 import config

    monkeypatch.setattr(config.get_settings(), "secret_kek", "", raising=False)

    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post(
                "/api/v1/secrets",
                json={"name": "gh", "value": "ghp_secret"},
                headers=_headers(tenant),
            )
            assert r.status_code == 503, r.text


async def test_create_secret_with_kind_totp_is_refused(app_session: AppSessionFactory) -> None:
    """F1: `POST /secrets` may not MINT a `kind="totp"` row -- that kind is
    reserved for `/auth/totp/confirm`'s own vault write."""
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post(
                "/api/v1/secrets",
                json={"name": f"totp:{uuid.uuid4()}", "value": "attacker-secret", "kind": "totp"},
                headers=_headers(tenant),
            )
            assert r.status_code == 403, r.text

    async with app_session(tenant) as db:
        rows = (await db.execute(m.Secret.__table__.select())).fetchall()
        assert rows == []


async def test_create_secret_cannot_overwrite_an_existing_totp_secret_via_a_different_kind(
    app_session: AppSessionFactory,
) -> None:
    """F1's real exploit: an attacker who leaves `kind` at its "generic"
    default (or picks anything other than "totp") must still be refused when
    `name` already names a row `/auth/totp/confirm` created -- `store_secret`
    upserts by `(tenant_id, name)` alone, so checking only the REQUESTED kind
    would have let this overwrite straight through."""
    tenant = uuid.uuid4()
    ref = f"totp:{uuid.uuid4()}"
    async with app_session(tenant) as db:
        # Directly seed a totp-kind secret the way /confirm would, without
        # going through the whole enroll/confirm flow.
        await store_secret(db, tenant_id=tenant, name=ref, value="real-totp-secret", kind="totp")
        await db.commit()

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post(
                "/api/v1/secrets",
                json={"name": ref, "value": "attacker-secret", "kind": "generic"},
                headers=_headers(tenant),
            )
            assert r.status_code == 403, r.text

    async with app_session(tenant) as db:
        still_totp = (
            await db.execute(select(m.Secret.kind).where(m.Secret.name == ref))
        ).scalar_one()
        assert still_totp == "totp"
        assert await resolve_secret(db, tenant_id=tenant, ref=ref) == "real-totp-secret"


async def test_list_secrets_excludes_totp_kind_rows(app_session: AppSessionFactory) -> None:
    """F1: an operator's `GET /secrets` must not surface a member's TOTP
    secret ref as if it were an ordinary, operator-deletable credential."""
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            create_r = await client.post(
                "/api/v1/secrets",
                json={"name": "gh", "value": "ghp_secret", "kind": "api_key"},
                headers=_headers(tenant),
            )
            assert create_r.status_code == 201, create_r.text

    async with app_session(tenant) as db:
        await store_secret(
            db, tenant_id=tenant, name=f"totp:{uuid.uuid4()}", value="s", kind="totp"
        )
        await db.commit()

    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            list_r = await client.get("/api/v1/secrets", headers=_headers(tenant))
            assert list_r.status_code == 200, list_r.text
            names = {row["name"] for row in list_r.json()}
            assert names == {"gh"}


async def test_delete_secret_with_kind_totp_is_refused(app_session: AppSessionFactory) -> None:
    """F1: `DELETE /secrets/{id}` may not remove a `kind="totp"` row -- doing
    so would silently lock the member out of their own second factor."""
    tenant = uuid.uuid4()
    ref = f"totp:{uuid.uuid4()}"
    async with app_session(tenant) as db:
        row = await store_secret(
            db, tenant_id=tenant, name=ref, value="real-totp-secret", kind="totp"
        )
        await db.commit()
        secret_id = row.id

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.delete(f"/api/v1/secrets/{secret_id}", headers=_headers(tenant))
            assert r.status_code == 403, r.text

    async with app_session(tenant) as db:
        still_there = await db.get(m.Secret, secret_id)
        assert still_there is not None
