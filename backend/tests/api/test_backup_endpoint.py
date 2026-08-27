"""`POST /api/v1/backup/{export,preview,restore}`: permission gates, the
export/preview round trip, and the confirmation gate on a destructive
restore. Error mapping (malformed archive, wrong passphrase, confirm_name
mismatch) all land on a clean 422, never a 500 -- see `backup.py`'s
`BackupError` handling."""

from __future__ import annotations

import io
import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from oc8.secrets.service import resolve_secret, store_secret
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    """`store_secret`/`resolve_secret` (used by the wrong-passphrase test)
    need a `secret_kek` -- same fixture as `tests/backup/test_restore_service.py`."""
    import base64

    from oc8.config import get_settings

    monkeypatch.setenv("OC8_SECRET_KEK", base64.b64encode(bytes(range(32))).decode())
    get_settings.cache_clear()


async def _seed_tenant(
    app_session: AppSessionFactory, tenant_id: uuid.UUID, *, name: str
) -> uuid.UUID:
    """A real Organization (with a KNOWN name, for the confirm_name gate), a
    Department and an Agent -- enough for export/preview/restore to all be
    meaningful. Returns the agent id."""
    async with app_session(tenant_id) as db:
        db.add(
            m.Organization(id=tenant_id, slug=f"tenant-{tenant_id.hex[:8]}", name=name, region="eu")
        )
        await db.flush()
        dept = m.Department(tenant_id=tenant_id, name="Vertrieb", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant_id,
            department_id=dept.id,
            name="Nora",
            narrowing={},
            definition={},
            presentation={},
        )
        db.add(agent)
        await db.flush()
        return agent.id


def _headers(tenant_id: uuid.UUID, *, role: str) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant_id, subject="dev-user", role=role)
    return {"Authorization": f"Bearer {token}"}


async def test_export_requires_backup_export_permission(app_session: AppSessionFactory) -> None:
    tenant_id = uuid.uuid4()
    await _seed_tenant(app_session, tenant_id, name="Acme GmbH")
    headers = _headers(tenant_id, role="member")

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post("/api/v1/backup/export", json={}, headers=headers)
            assert response.status_code == 403


async def test_restore_requires_backup_restore_permission(app_session: AppSessionFactory) -> None:
    tenant_id = uuid.uuid4()
    await _seed_tenant(app_session, tenant_id, name="Acme GmbH")
    headers = _headers(tenant_id, role="member")

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            files = {"file": ("backup.tar.gz", io.BytesIO(b"irrelevant"), "application/gzip")}
            response = await client.post("/api/v1/backup/preview", files=files, headers=headers)
            assert response.status_code == 403


async def test_export_then_preview_round_trip(app_session: AppSessionFactory) -> None:
    tenant_id = uuid.uuid4()
    await _seed_tenant(app_session, tenant_id, name="Acme GmbH")
    headers = _headers(tenant_id, role="org_admin")

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            export_response = await client.post("/api/v1/backup/export", json={}, headers=headers)
            assert export_response.status_code == 200
            assert export_response.headers["content-type"] == "application/gzip"
            assert "attachment" in export_response.headers["content-disposition"]

            files = {
                "file": ("backup.tar.gz", io.BytesIO(export_response.content), "application/gzip")
            }
            preview_response = await client.post(
                "/api/v1/backup/preview", files=files, headers=headers
            )
            assert preview_response.status_code == 200
            body = preview_response.json()
            assert body["problems"] == []
            assert body["has_secrets"] is False


async def test_restore_rejects_a_confirm_name_mismatch(app_session: AppSessionFactory) -> None:
    tenant_id = uuid.uuid4()
    await _seed_tenant(app_session, tenant_id, name="Acme GmbH")
    headers = _headers(tenant_id, role="org_admin")

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            export_response = await client.post("/api/v1/backup/export", json={}, headers=headers)
            files = {
                "file": ("backup.tar.gz", io.BytesIO(export_response.content), "application/gzip")
            }
            response = await client.post(
                "/api/v1/backup/restore",
                files=files,
                data={"confirm_name": "definitely not the company name"},
                headers=headers,
            )
            assert response.status_code == 422


async def test_restore_succeeds_with_the_exact_current_name(
    app_session: AppSessionFactory,
) -> None:
    tenant_id = uuid.uuid4()
    agent_id = await _seed_tenant(app_session, tenant_id, name="Acme GmbH")
    headers = _headers(tenant_id, role="org_admin")

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            export_response = await client.post("/api/v1/backup/export", json={}, headers=headers)
            assert export_response.status_code == 200

            async with app_session(tenant_id) as db:
                await db.execute(delete(m.Agent).where(m.Agent.id == agent_id))

            files = {
                "file": ("backup.tar.gz", io.BytesIO(export_response.content), "application/gzip")
            }
            response = await client.post(
                "/api/v1/backup/restore",
                files=files,
                data={"confirm_name": "Acme GmbH"},
                headers=headers,
            )
            assert response.status_code == 200
            body = response.json()
            assert body["tables"]["agent"] == 1

    async with app_session(tenant_id) as db:
        restored = (
            await db.execute(m.Agent.__table__.select().where(m.Agent.id == agent_id))
        ).all()
        assert len(restored) == 1


async def test_restore_rejects_a_malformed_archive(app_session: AppSessionFactory) -> None:
    tenant_id = uuid.uuid4()
    await _seed_tenant(app_session, tenant_id, name="Acme GmbH")
    headers = _headers(tenant_id, role="org_admin")

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            files = {
                "file": ("backup.tar.gz", io.BytesIO(b"not a real archive"), "application/gzip")
            }
            response = await client.post(
                "/api/v1/backup/restore",
                files=files,
                data={"confirm_name": "Acme GmbH"},
                headers=headers,
            )
            assert response.status_code == 422


async def test_preview_rejects_a_malformed_archive(app_session: AppSessionFactory) -> None:
    tenant_id = uuid.uuid4()
    await _seed_tenant(app_session, tenant_id, name="Acme GmbH")
    headers = _headers(tenant_id, role="org_admin")

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            files = {
                "file": ("backup.tar.gz", io.BytesIO(b"not a real archive"), "application/gzip")
            }
            response = await client.post("/api/v1/backup/preview", files=files, headers=headers)
            assert response.status_code == 422


async def test_restore_with_a_wrong_passphrase_is_422_and_writes_nothing(
    app_session: AppSessionFactory,
) -> None:
    tenant_id = uuid.uuid4()
    await _seed_tenant(app_session, tenant_id, name="Acme GmbH")
    async with app_session(tenant_id) as db:
        await store_secret(db, tenant_id=tenant_id, name="odoo/password", value="hunter2")
    headers = _headers(tenant_id, role="org_admin")

    async with app_session(tenant_id) as before_db:
        before = (
            await before_db.execute(
                m.Agent.__table__.select().where(m.Agent.tenant_id == tenant_id)
            )
        ).all()

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            export_response = await client.post(
                "/api/v1/backup/export", json={"passphrase": "right-one"}, headers=headers
            )
            assert export_response.status_code == 200

            files = {
                "file": ("backup.tar.gz", io.BytesIO(export_response.content), "application/gzip")
            }
            response = await client.post(
                "/api/v1/backup/restore",
                files=files,
                data={"confirm_name": "Acme GmbH", "passphrase": "wrong-one"},
                headers=headers,
            )
            assert response.status_code == 422

    async with app_session(tenant_id) as db:
        after = (
            await db.execute(m.Agent.__table__.select().where(m.Agent.tenant_id == tenant_id))
        ).all()
        assert {row.id for row in after} == {row.id for row in before}
        assert await resolve_secret(db, tenant_id=tenant_id, ref="odoo/password") == "hunter2"
