from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from oc8 import models as m
from oc8.knowledge.connectors import registry
from oc8.knowledge.connectors.base import (
    AuthContext,
    Connector,
    RawDocument,
    SourceItemMeta,
    ValidationResult,
)
from oc8.knowledge.ingest import run_source_sync
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


class _NeedsAuthConnector:
    type_id = "test_oauth_conn"
    requires_oauth = "google"
    config_schema: dict[str, Any] = {"type": "object", "properties": {}}
    seen_tokens: list[str] = []

    async def validate(
        self, config: dict[str, Any], auth: AuthContext | None = None
    ) -> ValidationResult:
        return ValidationResult(ok=True)

    async def discover(
        self, config: dict[str, Any], auth: AuthContext | None = None
    ) -> list[SourceItemMeta]:
        return [SourceItemMeta(uri="test://1", title="one")]

    async def fetch(
        self,
        config: dict[str, Any],
        cursor: dict[str, Any] | None,
        auth: AuthContext | None = None,
    ) -> AsyncIterator[RawDocument]:
        assert auth is not None, "sync must supply an AuthContext for an oauth connector"
        token = await auth.token()
        type(self).seen_tokens.append(token)
        yield RawDocument(
            source_uri="test://1",
            title="one",
            content="hello world",
            content_type="text/plain",
            content_hash="h1",
        )


def test_existing_connectors_declare_requires_oauth_none() -> None:
    for type_id in ("upload", "website"):
        assert registry.get_connector(type_id).requires_oauth is None


def test_existing_connectors_still_satisfy_the_protocol() -> None:
    for type_id in ("upload", "website"):
        assert isinstance(registry.get_connector(type_id), Connector)


async def test_sync_passes_an_auth_context_for_oauth_sources(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn_obj = _NeedsAuthConnector()
    _NeedsAuthConnector.seen_tokens = []
    monkeypatch.setitem(registry._CONNECTORS, conn_obj.type_id, conn_obj)

    tenant = uuid.uuid4()

    async def _fake_token(
        db: Any, *, tenant_id: uuid.UUID, connection_id: uuid.UUID
    ) -> str:
        return "token-abc"

    monkeypatch.setattr("oc8.knowledge.connectors.context.get_access_token", _fake_token)

    async with app_session(tenant) as db:
        kb = m.KnowledgeBase(
            tenant_id=tenant, name="kb", description="", embedding_model="nomic-embed-text"
        )
        db.add(kb)
        conn = m.OAuthConnection(
            id=uuid.uuid4(),
            tenant_id=tenant,
            provider="google",
            account_label="a@example.com",
            scopes=["email"],
            access_secret_ref="x",
            status="active",
            client_source="platform",
        )
        db.add(conn)
        await db.flush()
        ds = m.DataSource(
            tenant_id=tenant,
            connector_type=conn_obj.type_id,
            name="src",
            config={},
            oauth_connection_id=conn.id,
        )
        db.add(ds)
        await db.flush()

        job = await run_source_sync(db, tenant_id=tenant, data_source=ds, kb_id=kb.id)
        assert job.status in ("succeeded", "partial"), job.stats
        assert _NeedsAuthConnector.seen_tokens == ["token-abc"]


async def test_a_source_without_a_connection_still_gets_a_context(
    app_session: AppSessionFactory,
) -> None:
    """Sync always supplies an AuthContext, even with no OAuth connection: a
    connector may still need a stored secret (S3 keys). It is token() that
    refuses, not the absence of the context."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        kb = m.KnowledgeBase(
            tenant_id=tenant, name="kb2", description="", embedding_model="nomic-embed-text"
        )
        db.add(kb)
        await db.flush()
        ds = m.DataSource(
            tenant_id=tenant,
            connector_type="upload",
            name="up",
            config={
                "filename": "a.txt",
                "content": "hello",
                "content_type": "text/plain",
            },
        )
        db.add(ds)
        await db.flush()
        job = await run_source_sync(db, tenant_id=tenant, data_source=ds, kb_id=kb.id)
        assert job.status == "succeeded", job.stats


async def test_token_refuses_when_the_source_has_no_connected_account(
    app_session: AppSessionFactory,
) -> None:
    from oc8.knowledge.connectors.base import ConnectorError
    from oc8.knowledge.connectors.context import SourceAuthContext

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        ctx = SourceAuthContext(db, tenant_id=tenant, connection_id=None)
        with pytest.raises(ConnectorError):
            await ctx.token()


async def test_secret_lookup_is_tenant_scoped_and_reports_a_missing_ref(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import base64

    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )
    from oc8.knowledge.connectors.base import ConnectorError
    from oc8.knowledge.connectors.context import SourceAuthContext
    from oc8.secrets.service import store_secret

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    async with app_session(tenant_a) as db:
        await store_secret(db, tenant_id=tenant_a, name="s3/key", value="AKIA-secret")
    async with app_session(tenant_a) as db:
        ctx = SourceAuthContext(db, tenant_id=tenant_a)
        assert await ctx.secret("s3/key") == "AKIA-secret"
        with pytest.raises(ConnectorError):
            await ctx.secret("does/not/exist")
    # Tenant B stored nothing under that name and must not see tenant A's value.
    async with app_session(tenant_b) as db:
        with pytest.raises(ConnectorError):
            await SourceAuthContext(db, tenant_id=tenant_b).secret("s3/key")
