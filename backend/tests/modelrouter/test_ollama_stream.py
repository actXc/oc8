"""OllamaAdapter.stream() -- /api/chat's own newline-delimited JSON, not SSE.

A local model reports each tool call as one complete object per line rather
than fragmenting its arguments token by token, so a call becomes one delta
carrying its whole arguments in one shot. The final `"done": true` line
carries prompt_eval_count/eval_count -- Ollama's own names for input/output
tokens -- and is the only place usage or stop_reason appears.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from oc8.modelrouter.adapters.ollama import OllamaAdapter
from oc8.modelrouter.types import CompletionRequest, ModelParams, NeutralMessage, Usage

pytestmark = pytest.mark.asyncio


def _req() -> CompletionRequest:
    return CompletionRequest(
        provider="ollama",
        model="qwen2.5:3b",
        messages=[NeutralMessage(role="user", content="hi")],
        params=ModelParams(temperature=0.3, max_tokens=64),
    )


def _ndjson(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode()


class _FakeStream:
    def __init__(self, body: bytes, *, status_code: int = 200) -> None:
        self._resp = httpx.Response(
            status_code,
            content=body,
            headers={"content-type": "application/x-ndjson"},
            request=httpx.Request("POST", "http://ollama/api/chat"),
        )

    async def __aenter__(self) -> httpx.Response:
        return self._resp

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _patch_stream(monkeypatch: pytest.MonkeyPatch, body: bytes, *, status_code: int = 200) -> None:
    def fake_stream(self: httpx.AsyncClient, method: str, url: str, **kw: Any) -> _FakeStream:
        return _FakeStream(body, status_code=status_code)

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)


async def _chunks(adapter: OllamaAdapter) -> list[Any]:
    return [c async for c in adapter.stream(_req())]


async def test_text_chunks_arrive_in_order_and_the_final_line_carries_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _ndjson(
        '{"message":{"role":"assistant","content":"Hal"},"done":false}',
        '{"message":{"role":"assistant","content":"lo"},"done":false}',
        '{"done":true,"prompt_eval_count":12,"eval_count":3}',
    )
    _patch_stream(monkeypatch, body)
    chunks = await _chunks(OllamaAdapter(base_url="http://ollama"))
    assert "".join(c.text for c in chunks) == "Hallo"
    assert chunks[-1].usage == Usage(tokens_in=12, tokens_out=3)
    assert chunks[-1].stop_reason == "stop"


async def test_a_tool_call_arrives_whole_in_one_line(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _ndjson(
        '{"message":{"role":"assistant","content":"",'
        '"tool_calls":[{"function":{"name":"create_record","arguments":{"model":1}}}]},'
        '"done":false}',
        '{"done":true,"prompt_eval_count":12,"eval_count":3}',
    )
    _patch_stream(monkeypatch, body)
    chunks = await _chunks(OllamaAdapter(base_url="http://ollama"))
    deltas = [d for c in chunks for d in c.tool_calls]
    assert len(deltas) == 1
    assert deltas[0].name == "create_record"
    assert deltas[0].arguments_fragment == '{"model": 1}'
    assert chunks[-1].stop_reason == "tool_use"


async def test_two_tool_calls_across_lines_get_distinct_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _ndjson(
        '{"message":{"role":"assistant","content":"",'
        '"tool_calls":[{"function":{"name":"a","arguments":{}}}]},"done":false}',
        '{"message":{"role":"assistant","content":"",'
        '"tool_calls":[{"function":{"name":"b","arguments":{}}}]},"done":false}',
        '{"done":true,"prompt_eval_count":1,"eval_count":1}',
    )
    _patch_stream(monkeypatch, body)
    chunks = await _chunks(OllamaAdapter(base_url="http://ollama"))
    deltas = [d for c in chunks for d in c.tool_calls]
    assert [d.index for d in deltas] == [0, 1]
    assert [d.name for d in deltas] == ["a", "b"]


async def test_a_line_with_no_content_no_tool_calls_and_not_done_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _ndjson(
        '{"message":{"role":"assistant","content":""},"done":false}',
        '{"message":{"role":"assistant","content":"hi"},"done":false}',
        '{"done":true,"prompt_eval_count":1,"eval_count":1}',
    )
    _patch_stream(monkeypatch, body)
    chunks = await _chunks(OllamaAdapter(base_url="http://ollama"))
    assert "".join(c.text for c in chunks) == "hi"


async def test_an_error_status_raises_with_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stream(monkeypatch, b'{"error":"model not found"}', status_code=404)
    with pytest.raises(httpx.HTTPStatusError, match="model not found"):
        await _chunks(OllamaAdapter(base_url="http://ollama"))


async def test_a_malformed_line_does_not_kill_the_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    body = (
        b'{"message":{"role":"assistant","content":"a"},"done":false}\n'
        b"{not json}\n"
        b'{"message":{"role":"assistant","content":"b"},"done":false}\n'
        b'{"done":true,"prompt_eval_count":1,"eval_count":1}\n'
    )
    _patch_stream(monkeypatch, body)
    chunks = await _chunks(OllamaAdapter(base_url="http://ollama"))
    assert "".join(c.text for c in chunks) == "ab"
