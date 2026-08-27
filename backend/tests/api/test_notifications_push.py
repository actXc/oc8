# backend/tests/api/test_notifications_push.py
from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _token(
    role: str = "member", subject: str = "push-test-user", tenant: uuid.UUID | None = None
) -> str:
    return get_identity_provider().mint(
        tenant_id=tenant or uuid.UUID(str(ACME_TENANT_ID)), subject=subject, role=role
    )


async def test_get_vapid_public_key_requires_auth() -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get("/api/v1/notifications/push/vapid-public-key")
            assert r.status_code == 401


async def test_get_vapid_public_key_returns_configured_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oc8.config import get_settings

    monkeypatch.setenv("OC8_VAPID_PUBLIC_KEY", "test-public-key")
    get_settings.cache_clear()
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token()}"}
            r = await client.get("/api/v1/notifications/push/vapid-public-key", headers=headers)
            assert r.status_code == 200
            assert r.json()["publicKey"] == "test-public-key"
    get_settings.cache_clear()


async def test_post_subscription_creates_row_for_caller(
    app_session: AppSessionFactory,
) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token()}"}
            body = {
                "endpoint": "https://push.example.com/v1/new-sub",
                "keys": {"p256dh": "p", "auth": "a"},
            }
            r = await client.post(
                "/api/v1/notifications/push/subscriptions", json=body, headers=headers
            )
            assert r.status_code == 204, r.text

    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        result = await db.execute(
            select(m.PushSubscription).where(
                m.PushSubscription.endpoint == "https://push.example.com/v1/new-sub"
            )
        )
        row = result.scalar_one_or_none()
        assert row is not None
        assert row.p256dh == "p"
        assert row.auth == "a"


async def test_post_subscription_upserts_by_endpoint(app_session: AppSessionFactory) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token()}"}
            body1 = {
                "endpoint": "https://push.example.com/v1/upsert-me",
                "keys": {"p256dh": "old", "auth": "a"},
            }
            body2 = {
                "endpoint": "https://push.example.com/v1/upsert-me",
                "keys": {"p256dh": "new", "auth": "a"},
            }
            await client.post(
                "/api/v1/notifications/push/subscriptions", json=body1, headers=headers
            )
            r = await client.post(
                "/api/v1/notifications/push/subscriptions", json=body2, headers=headers
            )
            assert r.status_code == 204

    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        result = await db.execute(
            select(m.PushSubscription).where(
                m.PushSubscription.endpoint == "https://push.example.com/v1/upsert-me"
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1
        assert rows[0].p256dh == "new"


async def test_post_subscription_rejects_hijack_of_another_members_endpoint(
    app_session: AppSessionFactory,
) -> None:
    """A second member cannot steal ownership of a first member's endpoint by
    re-POSTing it with their own token -- see security review on Task 4: the
    original upsert-by-endpoint logic reassigned `member_id` unconditionally,
    letting anyone who learned an endpoint value (visible client-side, not a
    secret) silently take over someone else's subscription row."""
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            owner_headers = {"Authorization": f"Bearer {_token(subject='push-owner')}"}
            attacker_headers = {"Authorization": f"Bearer {_token(subject='push-attacker')}"}
            owner_body = {
                "endpoint": "https://push.example.com/v1/contested",
                "keys": {"p256dh": "owner-key", "auth": "owner-auth"},
            }
            attacker_body = {
                "endpoint": "https://push.example.com/v1/contested",
                "keys": {"p256dh": "attacker-key", "auth": "attacker-auth"},
            }
            owner_resp = await client.post(
                "/api/v1/notifications/push/subscriptions",
                json=owner_body,
                headers=owner_headers,
            )
            assert owner_resp.status_code == 204

            attacker_resp = await client.post(
                "/api/v1/notifications/push/subscriptions",
                json=attacker_body,
                headers=attacker_headers,
            )
            assert attacker_resp.status_code == 409

    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        owner_member = (
            await db.execute(select(m.OrgMember).where(m.OrgMember.subject == "push-owner"))
        ).scalar_one()
        result = await db.execute(
            select(m.PushSubscription).where(
                m.PushSubscription.endpoint == "https://push.example.com/v1/contested"
            )
        )
        row = result.scalar_one()
        assert row.member_id == owner_member.id
        assert row.p256dh == "owner-key"
        assert row.auth == "owner-auth"


async def test_same_endpoint_can_be_registered_under_two_tenants(
    app_session: AppSessionFactory,
) -> None:
    """One browser, one push service, one VAPID key pair -- so an operator who
    belongs to two tenants presents the SAME endpoint under both. Uniqueness is
    `(tenant_id, endpoint)`, so both subscribes succeed and each row stays in
    its own tenant. Under the original global UNIQUE the second one fell into
    the INSERT branch (RLS hid the first tenant's row from the lookup) and blew
    up as an unhandled IntegrityError -> 500."""
    endpoint = f"https://push.example.com/v1/shared-{uuid.uuid4().hex}"
    tenant_a = uuid.UUID(str(ACME_TENANT_ID))
    tenant_b = uuid.uuid4()
    body = {"endpoint": endpoint, "keys": {"p256dh": "p", "auth": "a"}}

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            for tenant in (tenant_a, tenant_b):
                headers = {
                    "Authorization": f"Bearer {_token(subject='multi-tenant-op', tenant=tenant)}"
                }
                r = await client.post(
                    "/api/v1/notifications/push/subscriptions", json=body, headers=headers
                )
                assert r.status_code == 204, (tenant, r.status_code, r.text)

    for tenant in (tenant_a, tenant_b):
        async with app_session(tenant) as db:
            rows = (
                (
                    await db.execute(
                        select(m.PushSubscription).where(m.PushSubscription.endpoint == endpoint)
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1, tenant
            assert rows[0].tenant_id == tenant


async def test_delete_subscription_removes_only_callers_own_row(
    app_session: AppSessionFactory,
) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token()}"}
            body = {
                "endpoint": "https://push.example.com/v1/to-delete",
                "keys": {"p256dh": "p", "auth": "a"},
            }
            await client.post(
                "/api/v1/notifications/push/subscriptions", json=body, headers=headers
            )
            r = await client.delete(
                "/api/v1/notifications/push/subscriptions",
                params={"endpoint": "https://push.example.com/v1/to-delete"},
                headers=headers,
            )
            assert r.status_code == 204

    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        result = await db.execute(
            select(m.PushSubscription).where(
                m.PushSubscription.endpoint == "https://push.example.com/v1/to-delete"
            )
        )
        assert result.scalar_one_or_none() is None


async def test_delete_subscription_cannot_remove_another_members_row(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        other_member = m.OrgMember(
            tenant_id=tenant, subject="someone-else", subject_uuid=uuid.uuid4()
        )
        db.add(other_member)
        await db.flush()
        db.add(
            m.PushSubscription(
                tenant_id=tenant,
                member_id=other_member.id,
                endpoint="https://push.example.com/v1/not-yours",
                p256dh="p",
                auth="a",
            )
        )
        await db.flush()

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token()}"}
            r = await client.delete(
                "/api/v1/notifications/push/subscriptions",
                params={"endpoint": "https://push.example.com/v1/not-yours"},
                headers=headers,
            )
            assert r.status_code == 204  # idempotent no-op, not an error

    async with app_session(tenant) as db:
        result = await db.execute(
            select(m.PushSubscription).where(
                m.PushSubscription.endpoint == "https://push.example.com/v1/not-yours"
            )
        )
        assert result.scalar_one_or_none() is not None
