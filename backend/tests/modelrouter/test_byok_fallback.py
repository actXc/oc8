from __future__ import annotations

import base64
import uuid

import pytest

from oc8 import models as m
from oc8.credentials.service import create_credential
from oc8.modelrouter.fallback import complete_with_fallback, stream_completion_with_fallback
from oc8.modelrouter.streaming import chunk_from_result
from oc8.modelrouter.types import CompletionRequest, CompletionResult, ModelParams, Usage
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )


class _CapturingRouter:
    """Records the api_key/base_url on each request it is asked to complete."""

    def __init__(self) -> None:
        self.keys: list[str | None] = []
        self.base_urls: list[str | None] = []

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        self.keys.append(req.api_key)
        self.base_urls.append(req.base_url)
        return CompletionResult(
            text="ok", tool_calls=[], usage=Usage(1, 1),
            stop_reason="stop", provider=req.provider, model=req.model,
        )

    async def stream(self, req: CompletionRequest) -> object:
        yield chunk_from_result(await self.complete(req))


async def test_primary_request_carries_the_tenant_key(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    router = _CapturingRouter()
    async with app_session(tenant) as db:
        await create_credential(
            db,
            tenant_id=tenant,
            name="Prod Anthropic",
            credential_type="anthropic_api_key",
            field_values={"api_key": "sk-tenant"},
        )
        await complete_with_fallback(
            db, router,  # type: ignore[arg-type]
            tenant_id=tenant, agent_id=uuid.uuid4(), primary=None,
            no_config_provider="anthropic", no_config_model="claude-x",
            messages=[], tools=[], params=ModelParams(), request_id=uuid.uuid4(),
            contains_restricted=False,
        )
    assert router.keys == ["sk-tenant"]


async def test_no_tenant_key_leaves_api_key_none(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    router = _CapturingRouter()
    async with app_session(tenant) as db:
        await complete_with_fallback(
            db, router,  # type: ignore[arg-type]
            tenant_id=tenant, agent_id=uuid.uuid4(), primary=None,
            no_config_provider="anthropic", no_config_model="claude-x",
            messages=[], tools=[], params=ModelParams(), request_id=uuid.uuid4(),
            contains_restricted=False,
        )
    assert router.keys == [None]


# ------------------------------------------------------------- base_url


async def test_no_config_request_carries_the_credentials_base_url(
    app_session: AppSessionFactory,
) -> None:
    """An agent with no ModelConfig at all (no_config_provider path) must
    still pick up a tenant's saved opaas.cloud-style endpoint -- an agent
    that has never been assigned a ModelConfig row is exactly the case a
    brand-new openai_compatible tenant hits first."""
    tenant = uuid.uuid4()
    router = _CapturingRouter()
    async with app_session(tenant) as db:
        await create_credential(
            db,
            tenant_id=tenant,
            name="opaas ai",
            credential_type="openai_compatible_api_key",
            field_values={"api_key": "sk-opaas", "base_url": "https://opaas.cloud/v1"},
        )
        await complete_with_fallback(
            db, router,  # type: ignore[arg-type]
            tenant_id=tenant, agent_id=uuid.uuid4(), primary=None,
            no_config_provider="openai_compatible", no_config_model="odoo-gpt",
            messages=[], tools=[], params=ModelParams(), request_id=uuid.uuid4(),
            contains_restricted=False,
        )
    assert router.base_urls == ["https://opaas.cloud/v1"]


async def test_primary_chain_request_carries_the_credentials_base_url(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    router = _CapturingRouter()
    async with app_session(tenant) as db:
        await create_credential(
            db,
            tenant_id=tenant,
            name="opaas ai",
            credential_type="openai_compatible_api_key",
            field_values={"api_key": "sk-opaas", "base_url": "https://opaas.cloud/v1"},
        )
        primary = m.ModelConfig(
            tenant_id=tenant, provider="openai_compatible", model="odoo-gpt", locality="cloud"
        )
        db.add(primary)
        await db.flush()
        await complete_with_fallback(
            db, router,  # type: ignore[arg-type]
            tenant_id=tenant, agent_id=uuid.uuid4(), primary=primary,
            no_config_provider="", no_config_model="",
            messages=[], tools=[], params=ModelParams(), request_id=uuid.uuid4(),
            contains_restricted=False,
        )
    assert router.base_urls == ["https://opaas.cloud/v1"]


async def test_a_params_base_url_overrides_the_credentials_one(
    app_session: AppSessionFactory,
) -> None:
    """`params.base_url` is a never-written-today, future per-ModelConfig
    override -- if something DOES set it, it must win over the tenant-wide
    credential, not be silently ignored."""
    tenant = uuid.uuid4()
    router = _CapturingRouter()
    async with app_session(tenant) as db:
        await create_credential(
            db,
            tenant_id=tenant,
            name="opaas ai",
            credential_type="openai_compatible_api_key",
            field_values={"api_key": "sk-opaas", "base_url": "https://opaas.cloud/v1"},
        )
        primary = m.ModelConfig(
            tenant_id=tenant,
            provider="openai_compatible",
            model="odoo-gpt",
            locality="cloud",
            params={"base_url": "https://override.example.com/v1"},
        )
        db.add(primary)
        await db.flush()
        await complete_with_fallback(
            db, router,  # type: ignore[arg-type]
            tenant_id=tenant, agent_id=uuid.uuid4(), primary=primary,
            no_config_provider="", no_config_model="",
            messages=[], tools=[], params=ModelParams(), request_id=uuid.uuid4(),
            contains_restricted=False,
        )
    assert router.base_urls == ["https://override.example.com/v1"]


async def test_streaming_request_also_carries_the_credentials_base_url(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    router = _CapturingRouter()
    async with app_session(tenant) as db:
        await create_credential(
            db,
            tenant_id=tenant,
            name="opaas ai",
            credential_type="openai_compatible_api_key",
            field_values={"api_key": "sk-opaas", "base_url": "https://opaas.cloud/v1"},
        )
        chunks = [
            c
            async for c in stream_completion_with_fallback(
                db, router,  # type: ignore[arg-type]
                tenant_id=tenant, agent_id=uuid.uuid4(), primary=None,
                no_config_provider="openai_compatible", no_config_model="odoo-gpt",
                messages=[], tools=[], params=ModelParams(), request_id=uuid.uuid4(),
                contains_restricted=False,
            )
        ]
    assert len(chunks) == 1
    assert router.base_urls == ["https://opaas.cloud/v1"]


async def test_no_credential_leaves_base_url_none(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    router = _CapturingRouter()
    async with app_session(tenant) as db:
        await complete_with_fallback(
            db, router,  # type: ignore[arg-type]
            tenant_id=tenant, agent_id=uuid.uuid4(), primary=None,
            no_config_provider="openai_compatible", no_config_model="odoo-gpt",
            messages=[], tools=[], params=ModelParams(), request_id=uuid.uuid4(),
            contains_restricted=False,
        )
    assert router.base_urls == [None]


# ------------------------------------------------------ credential_id (multi-account)


async def test_primarys_own_credential_id_wins_over_the_tenant_wide_pick(
    app_session: AppSessionFactory,
) -> None:
    """Two ModelConfigs of the same provider, each with its own
    `credential_id`, must resolve to their OWN bound credential -- this is
    the "connect multiple accounts" feature: without it, both ModelConfigs
    would silently share the tenant-wide "first by name" key/base_url no
    matter which credential they were meant to use."""
    tenant = uuid.uuid4()
    router = _CapturingRouter()
    async with app_session(tenant) as db:
        await create_credential(
            db,
            tenant_id=tenant,
            name="AAA (tenant-wide first)",
            credential_type="openai_compatible_api_key",
            field_values={"api_key": "sk-aaa", "base_url": "https://aaa.example.com/v1"},
        )
        second = await create_credential(
            db,
            tenant_id=tenant,
            name="ZZZ (not first)",
            credential_type="openai_compatible_api_key",
            field_values={"api_key": "sk-zzz", "base_url": "https://zzz.example.com/v1"},
        )
        primary = m.ModelConfig(
            tenant_id=tenant,
            provider="openai_compatible",
            model="odoo-gpt",
            locality="cloud",
            credential_id=second.id,
        )
        db.add(primary)
        await db.flush()
        await complete_with_fallback(
            db, router,  # type: ignore[arg-type]
            tenant_id=tenant, agent_id=uuid.uuid4(), primary=primary,
            no_config_provider="", no_config_model="",
            messages=[], tools=[], params=ModelParams(), request_id=uuid.uuid4(),
            contains_restricted=False,
        )
    assert router.keys == ["sk-zzz"]
    assert router.base_urls == ["https://zzz.example.com/v1"]


async def test_streaming_also_honors_the_configs_own_credential_id(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    router = _CapturingRouter()
    async with app_session(tenant) as db:
        await create_credential(
            db,
            tenant_id=tenant,
            name="AAA (tenant-wide first)",
            credential_type="openai_compatible_api_key",
            field_values={"api_key": "sk-aaa", "base_url": "https://aaa.example.com/v1"},
        )
        second = await create_credential(
            db,
            tenant_id=tenant,
            name="ZZZ (not first)",
            credential_type="openai_compatible_api_key",
            field_values={"api_key": "sk-zzz", "base_url": "https://zzz.example.com/v1"},
        )
        primary = m.ModelConfig(
            tenant_id=tenant,
            provider="openai_compatible",
            model="odoo-gpt",
            locality="cloud",
            credential_id=second.id,
        )
        db.add(primary)
        await db.flush()
        chunks = [
            c
            async for c in stream_completion_with_fallback(
                db, router,  # type: ignore[arg-type]
                tenant_id=tenant, agent_id=uuid.uuid4(), primary=primary,
                no_config_provider="", no_config_model="",
                messages=[], tools=[], params=ModelParams(), request_id=uuid.uuid4(),
                contains_restricted=False,
            )
        ]
    assert len(chunks) == 1
    assert router.keys == ["sk-zzz"]
    assert router.base_urls == ["https://zzz.example.com/v1"]
