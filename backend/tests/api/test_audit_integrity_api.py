from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8.audit.chain import append_event
from oc8.auth import get_identity_provider
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _token(tenant: uuid.UUID, role: str = "org_admin") -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role=role)


async def _seed(app_session: AppSessionFactory, tenant: uuid.UUID, n: int) -> None:
    async with app_session(tenant) as s:
        for i in range(n):
            await append_event(
                s,
                tenant_id=tenant,
                actor_type="system",
                actor_id=None,
                category="tool",
                action=f"a.{i}",
            )


async def test_integrity_is_never_before_the_first_run(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    await _seed(app_session, tenant, 2)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get(
                "/api/v1/audit/integrity",
                headers={"Authorization": f"Bearer {_token(tenant)}"},
            )
    assert r.status_code == 200
    assert r.json()["status"] == "never"
    assert r.json()["eventCount"] == 2


async def test_verify_then_integrity_reports_ok(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    await _seed(app_session, tenant, 3)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            h = {"Authorization": f"Bearer {_token(tenant)}"}
            v = await c.post("/api/v1/audit/verify", headers=h)
            g = await c.get("/api/v1/audit/integrity", headers=h)
    assert v.status_code == 200
    assert v.json()["status"] == "ok"
    assert v.json()["verifiedThroughSeq"] > 0
    assert len(v.json()["headHash"]) == 64
    assert g.json()["status"] == "ok"


async def test_full_verify_is_accepted(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    await _seed(app_session, tenant, 2)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/audit/verify?full=true",
                headers={"Authorization": f"Bearer {_token(tenant)}"},
            )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_break_kind_is_exposed_and_a_truncation_stays_broken(
    app_session: AppSessionFactory, pg_url: str
) -> None:
    """The operator screen needs the kind to give the right advice: a full
    check is the remedy for a hash mismatch but can never clear a truncation."""
    tenant = uuid.uuid4()
    await _seed(app_session, tenant, 4)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            h = {"Authorization": f"Bearer {_token(tenant)}"}
            ok = await c.post("/api/v1/audit/verify", headers=h)
            assert ok.json()["status"] == "ok"
            assert ok.json()["breakKind"] is None

            engine = sa.create_engine(pg_url)
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "DELETE FROM audit_event WHERE tenant_id = :t AND seq = "
                        "(SELECT max(seq) FROM audit_event WHERE tenant_id = :t)"
                    ),
                    {"t": str(tenant)},
                )
            engine.dispose()

            broken = await c.post("/api/v1/audit/verify", headers=h)
            assert broken.json()["status"] == "broken"
            assert broken.json()["breakKind"] == "truncation"

            # There is deliberately no acknowledgement endpoint, and the one
            # button the screen offers cannot help here.
            full = await c.post("/api/v1/audit/verify?full=true", headers=h)
            assert full.json()["status"] == "broken"
            assert full.json()["breakKind"] == "truncation"


async def test_middle_deletion_reports_missing_count(
    app_session: AppSessionFactory, pg_url: str
) -> None:
    """The truncation banner cannot claim entries after a given seq are gone
    -- a middle deletion proves that false: 5 events verify clean, row 3 (of
    5) is deleted, and nothing after seq 5 is actually missing. missingCount
    is the one figure the server can state exactly: verified_count minus what
    is still present."""
    tenant = uuid.uuid4()
    await _seed(app_session, tenant, 5)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            h = {"Authorization": f"Bearer {_token(tenant)}"}
            ok = await c.post("/api/v1/audit/verify", headers=h)
            assert ok.json()["status"] == "ok"
            assert ok.json()["missingCount"] is None

            victim_seq = None
            engine = sa.create_engine(pg_url)
            with engine.begin() as conn:
                victim_seq = conn.execute(
                    sa.text(
                        "SELECT seq FROM audit_event WHERE tenant_id = :t "
                        "ORDER BY seq LIMIT 1 OFFSET 2"
                    ),
                    {"t": str(tenant)},
                ).scalar_one()
                conn.execute(
                    sa.text("DELETE FROM audit_event WHERE seq = :s"), {"s": victim_seq}
                )
            engine.dispose()

            broken = await c.post("/api/v1/audit/verify", headers=h)
            assert broken.json()["status"] == "broken"
            assert broken.json()["breakKind"] == "truncation"
            # Exactly one row (the victim) is missing -- not "everything after
            # the reported seq", which for a middle deletion would be false.
            assert broken.json()["missingCount"] == 1

            g = await c.get("/api/v1/audit/integrity", headers=h)
            assert g.json()["missingCount"] == 1


async def test_member_is_forbidden_on_both(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    await _seed(app_session, tenant, 1)
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            h = {"Authorization": f"Bearer {_token(tenant, role='member')}"}
            g = await c.get("/api/v1/audit/integrity", headers=h)
            v = await c.post("/api/v1/audit/verify", headers=h)
    assert g.status_code == 403
    assert v.status_code == 403
