"""Streaming through the model router.

The router was entirely request/response (`"stream": False` everywhere). A real
agent harness streams, so the LLM gateway (§8.7 R1) needs a streaming path that
still reports usage -- an OpenAI-compatible upstream only sends usage in the
final chunk, and only when the request asks for it. Getting that wrong means an
aborted or unreported stream bills nothing, which makes the §15.4 budget blind.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from oc8.modelrouter.streaming import (
    estimate_tokens,
    stream_openai_sse,
    stream_with_fallback,
)
from oc8.modelrouter.types import (
    CompletionRequest,
    CompletionResult,
    ModelParams,
    NeutralMessage,
    ToolCall,
    Usage,
)

pytestmark = pytest.mark.asyncio


def _req() -> CompletionRequest:
    return CompletionRequest(
        provider="openai_compatible",
        model="m",
        messages=[NeutralMessage(role="user", content="hi")],
        params=ModelParams(temperature=0.3, max_tokens=64),
    )


def _sse(*lines: str) -> bytes:
    return ("".join(f"data: {line}\n\n" for line in lines)).encode()


def _client(body: bytes, *, capture: dict[str, object] | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            import json as _json

            capture["payload"] = _json.loads(request.content)
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_text_deltas_arrive_in_order() -> None:
    body = _sse(
        '{"choices":[{"delta":{"content":"Hal"}}]}',
        '{"choices":[{"delta":{"content":"lo"}}]}',
        '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "[DONE]",
    )
    async with _client(body) as c:
        chunks = [
            ch async for ch in stream_openai_sse("http://x/v1/chat/completions", {}, {}, client=c)
        ]
    assert "".join(ch.text for ch in chunks) == "Hallo"
    assert chunks[-1].stop_reason == "stop"


async def test_the_request_asks_for_usage_or_it_never_arrives() -> None:
    """An OpenAI-compatible upstream reports usage on a stream ONLY when the
    request carries stream_options.include_usage. Without it the gateway bills
    zero for every streamed call."""
    capture: dict[str, object] = {}
    async with _client(_sse("[DONE]"), capture=capture) as c:
        _ = [ch async for ch in stream_openai_sse("http://x", {"model": "m"}, {}, client=c)]
    payload = capture["payload"]
    assert isinstance(payload, dict)
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}


async def test_usage_from_the_final_chunk_is_surfaced() -> None:
    body = _sse(
        '{"choices":[{"delta":{"content":"ok"}}]}',
        '{"choices":[],"usage":{"prompt_tokens":120,"completion_tokens":7}}',
        "[DONE]",
    )
    async with _client(body) as c:
        chunks = [ch async for ch in stream_openai_sse("http://x", {}, {}, client=c)]
    reported = [ch.usage for ch in chunks if ch.usage is not None]
    assert reported == [Usage(tokens_in=120, tokens_out=7)]


async def test_tool_call_fragments_survive_with_their_index() -> None:
    """Arguments arrive split across chunks. The gateway forwards fragments to its
    client, so index/id/name must not be lost or merged across parallel calls."""
    body = _sse(
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
        '"function":{"name":"create_record","arguments":"{\\"mo"}}]}}]}',
        '{"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"arguments":"del\\":1}"}}]}}]}',
        '{"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        "[DONE]",
    )
    async with _client(body) as c:
        chunks = [ch async for ch in stream_openai_sse("http://x", {}, {}, client=c)]
    deltas = [d for ch in chunks for d in ch.tool_calls]
    assert [d.index for d in deltas] == [0, 0]
    assert deltas[0].id == "c1"
    assert deltas[0].name == "create_record"
    assert "".join(d.arguments_fragment for d in deltas) == '{"model":1}'
    assert chunks[-1].stop_reason == "tool_calls"


async def test_a_keepalive_or_malformed_line_does_not_kill_the_stream() -> None:
    """Providers emit comments and blank keepalives. Raising there would turn a
    cosmetic quirk into a failed run."""
    body = (
        b": keepalive\n\n"
        b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
        b"data: {not json}\n\n"
        b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    async with _client(body) as c:
        chunks = [ch async for ch in stream_openai_sse("http://x", {}, {}, client=c)]
    assert "".join(ch.text for ch in chunks) == "ab"


async def test_an_adapter_without_streaming_still_yields_one_chunk() -> None:
    """No provider may silently break because it has no streaming path: it
    degrades to a single chunk carrying the whole answer and its usage."""

    class _NonStreaming:
        provider = "ollama"

        async def complete(self, req: CompletionRequest) -> CompletionResult:
            return CompletionResult(
                text="ganze Antwort",
                tool_calls=[ToolCall(id="c1", name="t", arguments={"a": 1})],
                usage=Usage(tokens_in=5, tokens_out=3),
                stop_reason="tool_use",
                provider="ollama",
                model="m",
            )

    chunks = [ch async for ch in stream_with_fallback(_NonStreaming(), _req())]
    assert len(chunks) == 1
    assert chunks[0].text == "ganze Antwort"
    assert chunks[0].usage == Usage(tokens_in=5, tokens_out=3)
    assert chunks[0].stop_reason == "tool_use"
    # The whole tool call arrives at once, as one complete fragment.
    assert chunks[0].tool_calls[0].name == "t"
    assert chunks[0].tool_calls[0].arguments_fragment == '{"a": 1}'


async def test_a_streaming_adapter_is_preferred_when_present() -> None:
    class _Streaming:
        provider = "openai_compatible"

        async def complete(self, req: CompletionRequest) -> CompletionResult:  # pragma: no cover
            raise AssertionError("complete() must not be called when stream() exists")

        async def stream(self, req: CompletionRequest) -> AsyncIterator[object]:
            from oc8.modelrouter.types import CompletionChunk

            yield CompletionChunk(text="streamed")

    chunks = [ch async for ch in stream_with_fallback(_Streaming(), _req())]
    assert [ch.text for ch in chunks] == ["streamed"]


def test_the_token_estimate_is_only_used_when_nothing_was_reported() -> None:
    """A stream that ends without usage must not bill zero -- that would make a
    runaway agent invisible to its budget. The estimate is deliberately crude and
    named as an estimate; it exists so the number is non-zero, not so it is
    exact."""
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") >= 1
    # Monotonic in length, so more output can never bill less.
    assert estimate_tokens("a" * 400) > estimate_tokens("a" * 40)
