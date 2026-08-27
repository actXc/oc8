# backend/tests/modelrouter/test_classification.py
from __future__ import annotations

import pytest

from oc8.config import Settings
from oc8.modelrouter.router import ClassificationViolation, ModelRouter, locality_for_provider
from oc8.modelrouter.types import CompletionRequest, CompletionResult, NeutralMessage, Usage

pytestmark = pytest.mark.asyncio


def test_locality_for_provider_maps_ollama_variants_to_local() -> None:
    assert locality_for_provider("ollama") == "local"
    assert locality_for_provider("local") == "local"
    assert locality_for_provider("llama") == "local"


def test_locality_for_provider_maps_unknown_provider_to_local() -> None:
    # Unknown providers alias to ollama (matches ModelRouter.resolve()'s own
    # forgiving fallback — the local path always works without keys).
    assert locality_for_provider("some-unknown-provider") == "local"


def test_locality_for_provider_maps_known_cloud_providers_to_cloud() -> None:
    assert locality_for_provider("anthropic") == "cloud"
    assert locality_for_provider("claude") == "cloud"


class _FakeAdapter:
    provider = "fake"

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        return CompletionResult(
            text="ok",
            tool_calls=[],
            usage=Usage(tokens_in=1, tokens_out=1),
            stop_reason="stop",
            provider=req.provider,
            model=req.model,
        )


def _req(
    *,
    provider: str = "anthropic",
    model: str = "claude-x",
    contains_restricted: bool = False,
) -> CompletionRequest:
    return CompletionRequest(
        provider=provider,
        model=model,
        messages=[NeutralMessage(role="user", content="hi")],
        contains_restricted=contains_restricted,
    )


async def test_complete_raises_on_restricted_content_to_cloud_provider() -> None:
    router = ModelRouter(Settings(anthropic_api_key="key"))
    req = _req(contains_restricted=True)
    with pytest.raises(ClassificationViolation):
        await router.complete(req)


async def test_complete_allows_restricted_content_to_local_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ModelRouter,
        "_adapter",
        lambda self, canonical, *, base_url=None, api_key=None: _FakeAdapter(),
    )
    router = ModelRouter(Settings())
    req = _req(provider="ollama", model="llama3", contains_restricted=True)
    result = await router.complete(req)
    assert result.text == "ok"


async def test_complete_allows_non_restricted_content_to_cloud_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ModelRouter,
        "_adapter",
        lambda self, canonical, *, base_url=None, api_key=None: _FakeAdapter(),
    )
    router = ModelRouter(Settings(anthropic_api_key="key"))
    req = _req(contains_restricted=False)
    result = await router.complete(req)
    assert result.text == "ok"


async def test_complete_raises_before_touching_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    # The check must happen before adapter selection/dispatch, not after --
    # confirm _adapter is never called on the violation path.
    calls: list[str] = []

    def _track_adapter(
        self: ModelRouter,
        canonical: str,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> _FakeAdapter:
        calls.append(canonical)
        return _FakeAdapter()

    monkeypatch.setattr(ModelRouter, "_adapter", _track_adapter)
    router = ModelRouter(Settings(anthropic_api_key="key"))
    req = _req(contains_restricted=True)
    with pytest.raises(ClassificationViolation):
        await router.complete(req)
    assert calls == []
