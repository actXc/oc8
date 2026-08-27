"""Streaming through the ROUTER and the fallback chain.

The streaming path must not become a hole in the guards `complete()` applies:
strict BYOK and the classification/locality refusal. A streamed request that
skipped either would be a silent security regression -- restricted content could
leave the tenant's region simply by asking for a stream.

It also must not retry once bytes are out. A fallback after the first chunk would
send the client two overlapping answers on one response.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from oc8 import models as m
from oc8.config import get_settings
from oc8.modelrouter.fallback import stream_completion_with_fallback
from oc8.modelrouter.router import ClassificationViolation, ModelRouter, TenantKeyRequired
from oc8.modelrouter.types import (
    CompletionChunk,
    CompletionRequest,
    ModelParams,
    NeutralMessage,
    Usage,
)
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _req(**kw: Any) -> CompletionRequest:
    base: dict[str, Any] = dict(
        provider="openai",
        model="gpt-4o",
        messages=[NeutralMessage(role="user", content="hi")],
        params=ModelParams(),
        api_key="sk-test",
    )
    base.update(kw)
    return CompletionRequest(**base)


class _RecordingAdapter:
    provider = "openai"

    def __init__(self, chunks: list[CompletionChunk] | None = None) -> None:
        self.chunks = chunks or [CompletionChunk(text="ok", usage=Usage(1, 1))]
        self.called = False

    async def complete(self, req: CompletionRequest) -> Any:  # pragma: no cover
        raise AssertionError("stream path must not call complete()")

    async def stream(self, req: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        self.called = True
        for c in self.chunks:
            yield c


async def test_streaming_refuses_restricted_content_to_a_cloud_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same refusal complete() makes. Without it, asking for a stream would be
    a way to push restricted data to a cloud model."""
    router = ModelRouter(get_settings())
    adapter = _RecordingAdapter()
    monkeypatch.setattr(router, "_adapter", lambda *a, **k: adapter)

    with pytest.raises(ClassificationViolation):
        async for _ in router.stream(_req(contains_restricted=True)):
            pass
    assert adapter.called is False, "nothing may reach the provider"


async def test_streaming_enforces_strict_byok(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "require_tenant_model_key", True, raising=False)
    router = ModelRouter(get_settings())
    adapter = _RecordingAdapter()
    monkeypatch.setattr(router, "_adapter", lambda *a, **k: adapter)

    with pytest.raises(TenantKeyRequired):
        async for _ in router.stream(_req(api_key=None)):
            pass
    assert adapter.called is False


async def test_streaming_yields_the_adapters_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    router = ModelRouter(get_settings())
    adapter = _RecordingAdapter(
        [CompletionChunk(text="Hal"), CompletionChunk(text="lo", usage=Usage(2, 3))]
    )
    monkeypatch.setattr(router, "_adapter", lambda *a, **k: adapter)

    chunks = [c async for c in router.stream(_req())]
    assert "".join(c.text for c in chunks) == "Hallo"
    assert chunks[-1].usage == Usage(2, 3)


# ------------------------------------------------------------- fallback chain


async def _model_config(db: Any, tenant: uuid.UUID, **kw: Any) -> m.ModelConfig:
    cfg = m.ModelConfig(
        tenant_id=tenant, provider=kw.get("provider", "openai"),
        model=kw.get("model", "gpt-4o"), params=kw.get("params", {}),
        fallbacks=kw.get("fallbacks", []),
    )
    db.add(cfg)
    await db.flush()
    return cfg


class _FailsBeforeAnyChunk:
    provider = "openai"

    async def complete(self, req: CompletionRequest) -> Any:  # pragma: no cover
        raise AssertionError("not used")

    async def stream(self, req: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        raise httpx.ConnectError("upstream down")
        yield CompletionChunk()  # pragma: no cover


class _FailsAfterOneChunk:
    provider = "openai"

    async def complete(self, req: CompletionRequest) -> Any:  # pragma: no cover
        raise AssertionError("not used")

    async def stream(self, req: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        yield CompletionChunk(text="halb ")
        raise httpx.ConnectError("died mid-stream")


async def test_a_failure_before_the_first_chunk_falls_over(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing reached the client yet, so switching provider is safe and is what
    the §9.4 chain is for."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        second = await _model_config(db, tenant, model="gpt-4o-mini")
        primary = await _model_config(db, tenant, fallbacks=[str(second.id)])

        router = ModelRouter(get_settings())
        good = _RecordingAdapter([CompletionChunk(text="vom Zweiten", usage=Usage(1, 1))])
        adapters = iter([_FailsBeforeAnyChunk(), good])
        monkeypatch.setattr(router, "_adapter", lambda *a, **k: next(adapters))

        chunks = [
            c
            async for c in stream_completion_with_fallback(
                db, router, tenant_id=tenant, agent_id=uuid.uuid4(), primary=primary,
                no_config_provider="openai", no_config_model="gpt-4o",
                messages=[NeutralMessage(role="user", content="hi")], tools=[],
                params=ModelParams(), request_id=uuid.uuid4(), contains_restricted=False,
            )
        ]
    assert "".join(c.text for c in chunks) == "vom Zweiten"
    assert good.called is True


async def test_a_failure_AFTER_the_first_chunk_is_not_retried(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bytes are already on the wire. Falling over here would append a second,
    overlapping answer to the same response -- the client would see both."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        second = await _model_config(db, tenant, model="gpt-4o-mini")
        primary = await _model_config(db, tenant, fallbacks=[str(second.id)])

        router = ModelRouter(get_settings())
        never = _RecordingAdapter([CompletionChunk(text="DARF NICHT ERSCHEINEN")])
        adapters = iter([_FailsAfterOneChunk(), never])
        monkeypatch.setattr(router, "_adapter", lambda *a, **k: next(adapters))

        seen: list[str] = []
        with pytest.raises(httpx.ConnectError):
            async for c in stream_completion_with_fallback(
                db, router, tenant_id=tenant, agent_id=uuid.uuid4(), primary=primary,
                no_config_provider="openai", no_config_model="gpt-4o",
                messages=[NeutralMessage(role="user", content="hi")], tools=[],
                params=ModelParams(), request_id=uuid.uuid4(), contains_restricted=False,
            ):
                seen.append(c.text)

    assert seen == ["halb "]
    assert never.called is False, "the fallback must not run once output has started"


async def test_without_a_model_config_there_is_exactly_one_attempt(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        router = ModelRouter(get_settings())
        adapter = _RecordingAdapter()
        monkeypatch.setattr(router, "_adapter", lambda *a, **k: adapter)
        chunks = [
            c
            async for c in stream_completion_with_fallback(
                db, router, tenant_id=tenant, agent_id=uuid.uuid4(), primary=None,
                no_config_provider="ollama", no_config_model="llama3",
                messages=[NeutralMessage(role="user", content="hi")], tools=[],
                params=ModelParams(), request_id=uuid.uuid4(), contains_restricted=False,
            )
        ]
    assert [c.text for c in chunks] == ["ok"]
