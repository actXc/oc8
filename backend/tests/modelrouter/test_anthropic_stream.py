"""AnthropicAdapter.stream() -- Claude's own SSE event shapes.

Unlike the OpenAI-shaped providers, a payload's meaning here depends on its
`event:` line, not just its `data:` body: content_block_start carries a tool
call's id/name once, while its arguments stream afterward as text-fragment
input_json_delta events keyed by the same block index. message_start carries
input_tokens; message_delta carries only output_tokens and the stop_reason.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from oc8.modelrouter.adapters.anthropic import AnthropicAdapter
from oc8.modelrouter.types import CompletionRequest, ModelParams, NeutralMessage, Usage

pytestmark = pytest.mark.asyncio


def _req() -> CompletionRequest:
    return CompletionRequest(
        provider="anthropic",
        model="claude-sonnet-4-5",
        messages=[NeutralMessage(role="user", content="hi")],
        params=ModelParams(temperature=0.3, max_tokens=64),
    )


def _sse(*events: tuple[str, str]) -> bytes:
    out = b""
    for event, data in events:
        out += f"event: {event}\ndata: {data}\n\n".encode()
    return out


class _FakeStream:
    def __init__(self, body: bytes, *, status_code: int = 200) -> None:
        self._resp = httpx.Response(
            status_code,
            content=body,
            headers={"content-type": "text/event-stream"},
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
        )

    async def __aenter__(self) -> httpx.Response:
        return self._resp

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _patch_stream(monkeypatch: pytest.MonkeyPatch, body: bytes, *, status_code: int = 200) -> None:
    def fake_stream(self: httpx.AsyncClient, method: str, url: str, **kw: Any) -> _FakeStream:
        return _FakeStream(body, status_code=status_code)

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)


async def _chunks(adapter: AnthropicAdapter) -> list[Any]:
    return [c async for c in adapter.stream(_req())]


async def test_text_deltas_arrive_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _sse(
        ("content_block_start", '{"index":0,"content_block":{"type":"text","text":""}}'),
        ("content_block_delta", '{"index":0,"delta":{"type":"text_delta","text":"Hal"}}'),
        ("content_block_delta", '{"index":0,"delta":{"type":"text_delta","text":"lo"}}'),
        ("message_delta", '{"delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}}'),
    )
    _patch_stream(monkeypatch, body)
    chunks = await _chunks(AnthropicAdapter(api_key="sk-ant-test"))
    assert "".join(c.text for c in chunks) == "Hallo"
    assert chunks[-1].stop_reason == "end_turn"


async def test_a_tool_calls_id_and_name_arrive_on_content_block_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _sse(
        (
            "content_block_start",
            '{"index":0,"content_block":{"type":"tool_use","id":"toolu_1","name":"create_record","input":{}}}',
        ),
        (
            "content_block_delta",
            '{"index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"mo"}}',
        ),
        (
            "content_block_delta",
            '{"index":0,"delta":{"type":"input_json_delta","partial_json":"del\\":1}"}}',
        ),
        ("message_delta", '{"delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":9}}'),
    )
    _patch_stream(monkeypatch, body)
    chunks = await _chunks(AnthropicAdapter(api_key="sk-ant-test"))
    deltas = [d for c in chunks for d in c.tool_calls]
    assert deltas[0].id == "toolu_1"
    assert deltas[0].name == "create_record"
    assert "".join(d.arguments_fragment for d in deltas) == '{"model":1}'
    assert chunks[-1].stop_reason == "tool_use"


async def test_input_tokens_from_message_start_survive_past_message_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """message_delta's own usage carries output_tokens only. Since a caller
    treats each reported usage as the new cumulative total (not something to
    sum across chunks), the input-token count from message_start must still
    be present on the LAST usage-carrying chunk, or it reads as zero."""
    body = _sse(
        ("message_start", '{"message":{"usage":{"input_tokens":120,"output_tokens":0}}}'),
        ("content_block_delta", '{"index":0,"delta":{"type":"text_delta","text":"ok"}}'),
        ("message_delta", '{"delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":7}}'),
    )
    _patch_stream(monkeypatch, body)
    chunks = await _chunks(AnthropicAdapter(api_key="sk-ant-test"))
    usages = [c.usage for c in chunks if c.usage is not None]
    assert usages[-1] == Usage(tokens_in=120, tokens_out=7)


async def test_ping_and_content_block_stop_carry_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _sse(
        ("ping", "{}"),
        ("content_block_delta", '{"index":0,"delta":{"type":"text_delta","text":"a"}}'),
        ("content_block_stop", '{"index":0}'),
        ("message_stop", "{}"),
    )
    _patch_stream(monkeypatch, body)
    chunks = await _chunks(AnthropicAdapter(api_key="sk-ant-test"))
    assert "".join(c.text for c in chunks) == "a"


async def test_an_error_status_raises_with_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stream(
        monkeypatch, b'{"error":{"message":"overloaded"}}', status_code=529
    )
    with pytest.raises(httpx.HTTPStatusError, match="overloaded"):
        await _chunks(AnthropicAdapter(api_key="sk-ant-test"))


async def test_a_malformed_line_does_not_kill_the_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    body = (
        b"event: content_block_delta\n"
        b'data: {"index":0,"delta":{"type":"text_delta","text":"a"}}\n\n'
        b"event: content_block_delta\n"
        b"data: {not json}\n\n"
        b"event: content_block_delta\n"
        b'data: {"index":0,"delta":{"type":"text_delta","text":"b"}}\n\n'
    )
    _patch_stream(monkeypatch, body)
    chunks = await _chunks(AnthropicAdapter(api_key="sk-ant-test"))
    assert "".join(c.text for c in chunks) == "ab"
