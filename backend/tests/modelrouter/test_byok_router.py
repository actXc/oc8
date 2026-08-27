from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8.config import Settings
from oc8.modelrouter.router import ModelRouter, TenantKeyRequired
from oc8.modelrouter.types import CompletionRequest, ModelParams

pytestmark = pytest.mark.asyncio


def _req(
    provider: str, *, api_key: str | None = None, restricted: bool = False
) -> CompletionRequest:
    return CompletionRequest(
        provider=provider, model="m", messages=[], params=ModelParams(),
        tenant_id=uuid.uuid4(), api_key=api_key, contains_restricted=restricted,
    )


def test_resolve_stays_cloud_when_tenant_key_present_and_platform_empty() -> None:
    r = ModelRouter(Settings(anthropic_api_key=""))
    canonical, _ = r.resolve("anthropic", "claude-x", api_key="sk-tenant")
    assert canonical == "anthropic"


def test_resolve_falls_back_to_ollama_when_no_key_anywhere() -> None:
    r = ModelRouter(Settings(anthropic_api_key=""))
    canonical, _ = r.resolve("anthropic", "claude-x", api_key=None)
    assert canonical == "ollama"


async def test_strict_mode_refuses_cloud_without_a_tenant_key() -> None:
    r = ModelRouter(Settings(anthropic_api_key="sk-platform", require_tenant_model_key=True))
    with pytest.raises(TenantKeyRequired):
        await r.complete(_req("anthropic", api_key=None))


async def test_strict_mode_allows_cloud_with_a_tenant_key(monkeypatch: pytest.MonkeyPatch) -> None:
    r = ModelRouter(Settings(anthropic_api_key="", require_tenant_model_key=True))
    captured: dict[str, Any] = {}

    class _Spy:
        def __init__(self, key: str) -> None:
            captured["key"] = key

        async def complete(self, req: CompletionRequest) -> Any:
            from oc8.modelrouter.types import CompletionResult, Usage

            return CompletionResult(
                text="ok", tool_calls=[], usage=Usage(1, 1),
                stop_reason="stop", provider="anthropic", model="m",
            )

    monkeypatch.setattr(
        "oc8.modelrouter.router.build_adapter",
        lambda canonical, settings, base_url=None, api_key=None: _Spy(api_key or ""),
    )
    res = await r.complete(_req("anthropic", api_key="sk-tenant"))
    assert res.text == "ok"
    assert captured["key"] == "sk-tenant"


async def test_strict_mode_allows_local_without_a_key() -> None:
    r = ModelRouter(Settings(require_tenant_model_key=True))
    # ollama needs no key; must not raise. It will try to reach ollama and fail
    # at the HTTP layer, which is fine -- we only assert it does NOT raise
    # TenantKeyRequired.
    with pytest.raises(Exception) as exc:
        await r.complete(_req("ollama"))
    assert not isinstance(exc.value, TenantKeyRequired)


async def test_flag_off_uses_platform_key_no_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    r = ModelRouter(Settings(anthropic_api_key="sk-platform", require_tenant_model_key=False))

    class _Spy:
        def __init__(self, key: str) -> None:
            self.key = key

        async def complete(self, req: CompletionRequest) -> Any:
            from oc8.modelrouter.types import CompletionResult, Usage

            return CompletionResult(
                text="ok", tool_calls=[], usage=Usage(1, 1),
                stop_reason="stop", provider="anthropic", model="m",
            )

    monkeypatch.setattr(
        "oc8.modelrouter.router.build_adapter",
        lambda canonical, settings, base_url=None, api_key=None: _Spy(
            api_key or settings.anthropic_api_key
        ),
    )
    res = await r.complete(_req("anthropic", api_key=None))
    assert res.text == "ok"
