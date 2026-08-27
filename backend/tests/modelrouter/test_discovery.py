from __future__ import annotations

from typing import Any

import httpx
import pytest

from oc8.config import Settings
from oc8.modelrouter.adapters.openai_compatible import OpenAICompatibleAdapter
from oc8.modelrouter.discovery import DiscoveryError, discover_models

pytestmark = pytest.mark.asyncio


def _patch_get(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    async def fake_get(
        self: httpx.AsyncClient, url: str, headers: dict[str, str] | None = None, **kw: Any
    ) -> httpx.Response:
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)


async def test_anthropic_ids_come_from_the_data_array(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"data": [{"id": "claude-3-5-sonnet-20241022"}, {"id": "claude-3-opus"}]}
    _patch_get(monkeypatch, payload)
    models = await discover_models(
        "anthropic", settings=Settings(), base_url=None, api_key="sk-ant-test"
    )
    assert models == ["claude-3-5-sonnet-20241022", "claude-3-opus"]


async def test_anthropic_requires_a_key_before_any_request_is_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a: Any, **kw: Any) -> None:
        raise AssertionError("should never reach the network without a key")

    monkeypatch.setattr(httpx.AsyncClient, "get", boom)
    with pytest.raises(DiscoveryError, match="key is required"):
        await discover_models("anthropic", settings=Settings(), base_url=None, api_key=None)


async def test_openai_compatible_uses_the_given_base_url_and_sorts_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_get(monkeypatch, {"data": [{"id": "mistral-large"}, {"id": "codestral"}]})
    models = await discover_models(
        "openai_compatible",
        settings=Settings(),
        base_url="https://my-litellm.internal/v1",
        api_key="k",
    )
    assert models == ["codestral", "mistral-large"]


async def test_ollama_reads_the_tags_endpoint_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, {"models": [{"name": "llama3.1:8b"}, {"name": "mistral:7b"}]})
    models = await discover_models(
        "ollama", settings=Settings(), base_url="http://localhost:11434", api_key=None
    )
    assert models == ["llama3.1:8b", "mistral:7b"]


async def test_unhandled_canonical_raises_key_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider whose adapter isn't OpenAI-shaped at all (nothing generic
    to fall back to) -- not the "unknown canonical" case, which build_adapter
    itself already raises KeyError for."""
    from oc8.modelrouter import discovery as discovery_module

    class _NotOpenAIShaped:
        pass

    monkeypatch.setattr(
        discovery_module, "build_adapter", lambda *a, **kw: _NotOpenAIShaped()
    )
    with pytest.raises(KeyError):
        await discover_models(
            "some_plugin_provider", settings=Settings(), base_url=None, api_key=None
        )


async def test_unknown_canonical_raises_key_error() -> None:
    with pytest.raises(KeyError):
        await discover_models("totally_bogus", settings=Settings(), base_url=None, api_key=None)


async def test_a_plugin_provider_built_on_openai_compatible_adapter_discovers_generically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """opaas_ai_provider (and any future LiteLLM/vLLM-backed plugin) wraps
    OpenAICompatibleAdapter with its own DEFAULT_BASE_URL -- discover_models
    must find that adapter's real, provider-specific base_url/api_key
    through the SAME factory the completion path builds it with, not a
    hardcoded canonical allowlist that would only ever know the four
    built-ins."""
    from oc8.modelrouter import discovery as discovery_module

    def fake_build_adapter(
        canonical: str, settings: Settings, base_url: str | None, api_key: str | None
    ) -> OpenAICompatibleAdapter:
        assert canonical == "opaas_ai"
        return OpenAICompatibleAdapter(base_url or "https://ai.opaas.online/v1", api_key or "")

    monkeypatch.setattr(discovery_module, "build_adapter", fake_build_adapter)
    _patch_get(monkeypatch, {"data": [{"id": "mistral-large-latest"}]})
    models = await discover_models(
        "opaas_ai", settings=Settings(), base_url=None, api_key="sk-opaas"
    )
    assert models == ["mistral-large-latest"]
