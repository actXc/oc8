"""An upstream rejection keeps the provider's own explanation.

`resp.raise_for_status()` reports the status line and throws the body away --
but the body is where a provider actually SAYS what was wrong ("Unexpected role
'system' after role 'tool'"). Losing it turns every 4xx into an unexplained
failure and costs hours of guessing, so every provider call in the router
raises with the body attached. The streaming path matters most: that is what a
real harness uses, and there the body is discarded twice over, because a
streamed response has not even been read when the status is checked.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from oc8.modelrouter.adapters.anthropic import AnthropicAdapter
from oc8.modelrouter.adapters.ollama import OllamaAdapter
from oc8.modelrouter.adapters.openai_compatible import OpenAICompatibleAdapter
from oc8.modelrouter.fallback import is_retryable
from oc8.modelrouter.http_errors import explain_upstream_error
from oc8.modelrouter.streaming import stream_openai_sse
from oc8.modelrouter.types import CompletionRequest, ModelParams, NeutralMessage

pytestmark = pytest.mark.asyncio

# The real 400 from opaas_ai that started all this.
PROVIDER_MESSAGE = "Unexpected role 'system' after role 'tool'"


def _error_response(
    url: str, status_code: int = 400, message: str = PROVIDER_MESSAGE
) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={"error": {"message": message, "type": "invalid_request_error"}},
        request=httpx.Request("POST", url),
    )


def _req() -> CompletionRequest:
    return CompletionRequest(
        provider="openai_compatible",
        model="odoo-gpt",
        messages=[NeutralMessage(role="user", content="hi")],
        params=ModelParams(temperature=0.3, max_tokens=64),
    )


def _patch_post(
    monkeypatch: pytest.MonkeyPatch, status_code: int = 400, message: str = PROVIDER_MESSAGE
) -> None:
    async def fake_post(
        self: httpx.AsyncClient, url: str, json: Any = None, **kw: Any
    ) -> httpx.Response:
        return _error_response(url, status_code, message)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


async def test_a_rejected_completion_carries_the_providers_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_post(monkeypatch)
    adapter = OpenAICompatibleAdapter(base_url="https://api.opaas.ai/v1", api_key="k")

    with pytest.raises(httpx.HTTPStatusError) as exc:
        await adapter.complete(_req())

    assert PROVIDER_MESSAGE in str(exc.value)
    assert "400" in str(exc.value)


async def test_a_rejected_stream_carries_the_providers_message() -> None:
    """The streamed case: the body is not read when the status is checked, so a
    naive `resp.text` raises ResponseNotRead and the reason is lost entirely."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": PROVIDER_MESSAGE}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError) as exc:
        async for _chunk in stream_openai_sse(
            "https://api.opaas.ai/v1/chat/completions", {"model": "m"}, {}, client=client
        ):
            pass

    assert PROVIDER_MESSAGE in str(exc.value)


async def test_a_rejected_anthropic_call_carries_the_providers_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_post(monkeypatch, message="max_tokens: 200000 > 8192")
    adapter = AnthropicAdapter(api_key="sk-ant-test")

    with pytest.raises(httpx.HTTPStatusError) as exc:
        await adapter.complete(_req())

    assert "max_tokens: 200000 > 8192" in str(exc.value)


async def test_a_rejected_ollama_call_carries_the_providers_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_post(monkeypatch, status_code=404, message="model llama9 not found")
    adapter = OllamaAdapter(base_url="http://localhost:11434")

    with pytest.raises(httpx.HTTPStatusError) as exc:
        await adapter.complete(_req())

    assert "model llama9 not found" in str(exc.value)


async def test_a_rejected_embedding_carries_the_providers_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_post(monkeypatch, status_code=404, message="model nomic not found")
    adapter = OllamaAdapter(base_url="http://localhost:11434")

    with pytest.raises(httpx.HTTPStatusError) as exc:
        await adapter.embed("text", "nomic")

    assert "model nomic not found" in str(exc.value)


async def test_the_fallback_chain_still_classifies_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Carrying the body must not change what the error IS: a 5xx still falls
    over to the next model in the chain, a 400 still fails the request."""
    _patch_post(monkeypatch, status_code=503, message="upstream capacity")
    adapter = OpenAICompatibleAdapter(base_url="https://api.opaas.ai/v1")
    with pytest.raises(httpx.HTTPStatusError) as server_error:
        await adapter.complete(_req())
    assert is_retryable(server_error.value) is True

    _patch_post(monkeypatch, status_code=400)
    with pytest.raises(httpx.HTTPStatusError) as client_error:
        await adapter.complete(_req())
    assert is_retryable(client_error.value) is False


async def test_a_huge_error_body_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider that echoes the whole request back must not put a megabyte of
    prompt into the logs and into a 502 body."""
    _patch_post(monkeypatch, message="x" * 50_000)
    adapter = OpenAICompatibleAdapter(base_url="https://api.opaas.ai/v1")

    with pytest.raises(httpx.HTTPStatusError) as exc:
        await adapter.complete(_req())

    assert len(str(exc.value)) < 4_000


def test_a_context_window_overflow_says_so() -> None:
    """A conversation that outgrew the model's window comes back as a raw
    provider 400 about a NEGATIVE max_tokens.

    Live, 2026-07-28: `max_tokens must be at least 1, got -7075`. Nothing about
    that says "the transcript is 7000 tokens too long"; it reads like a
    misconfigured request or a broken provider, and the wrapper around it advises
    trying again in a moment -- which can never work, because the next attempt
    sends the same transcript. The number IS the deficit, so it can be said
    plainly.
    """
    raw = (
        '400 from https://ai.example/v1/chat/completions: {"error":{"message":'
        '"litellm.BadRequestError: Hosted_vllmException - {\\"error\\":{\\"message\\":'
        '\\"max_tokens must be at least 1, got -7075. (parameter=max_tokens, '
        'value=-7075)\\",\\"type\\":\\"BadRequestError\\"}}"}}'
    )
    explained = explain_upstream_error(raw)
    assert explained is not None
    assert "Kontextfenster" in explained or "context window" in explained.lower()
    assert "7075" in explained, "the deficit is the actionable part"
    assert "try again" not in explained.lower()


def test_an_ordinary_upstream_error_is_left_alone() -> None:
    """Only this one shape is rewritten. Anything else keeps the provider's own
    words, which are what a person needs in order to look it up."""
    assert explain_upstream_error("500 from https://ai.example: upstream exploded") is None
    assert explain_upstream_error("max_tokens must be at least 1, got 0") is None
