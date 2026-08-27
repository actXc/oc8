"""The Responses-API adapter behind a ChatGPT-subscription model connection.

Every JSON body below is the REAL Responses API shape, taken from OpenAI's own
machine-readable spec (openai/openai-openapi `openapi.yaml`) and from Codex's
own request builder -- see `oc8.modelrouter.adapters.chatgpt_subscription`'s
module docstring for the per-field citations. The shapes are deliberately not
the Chat Completions ones: a flat `input` array of typed items, flat function
tools, `output`/`output_text` instead of `choices[].message`, and
`input_tokens`/`output_tokens` instead of `prompt_tokens`/`completion_tokens`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from oc8.modelrouter.adapters.chatgpt_subscription import (
    ChatGptSubscriptionAdapter,
    build_responses_payload,
    parse_responses_result,
    stream_responses_sse,
)
from oc8.modelrouter.types import (
    CompletionRequest,
    ModelParams,
    NeutralMessage,
    NeutralTool,
    ToolCall,
)

# No `pytestmark = pytest.mark.asyncio`: `asyncio_mode = "auto"` already picks
# up the async tests, and the explicit mark would warn on every sync one.


def _req(**kw: Any) -> CompletionRequest:
    kw.setdefault("messages", [NeutralMessage(role="user", content="hallo")])
    return CompletionRequest(provider="openai_chatgpt", model="gpt-5-codex", **kw)


# --------------------------------------------------------------------------
# api_key / headers
# --------------------------------------------------------------------------


def test_init_parses_composite_api_key() -> None:
    adapter = ChatGptSubscriptionAdapter(
        base_url="https://chatgpt.com/backend-api/codex",
        api_key=json.dumps({"access_token": "at1", "account_id": "acct1"}),
    )
    assert adapter._access_token == "at1"
    assert adapter._account_id == "acct1"


def test_init_falls_back_to_bare_token_on_non_json_api_key() -> None:
    adapter = ChatGptSubscriptionAdapter(base_url="https://x", api_key="plain-token")
    assert adapter._access_token == "plain-token"
    assert adapter._account_id is None


@pytest.mark.parametrize(
    "api_key",
    ["", "not json at all", "[1, 2, 3]", "123", '"just-a-string"', '{"no_token": 1}', "null"],
)
def test_a_malformed_composite_key_never_raises(api_key: str) -> None:
    """Task 8's bridge is the only caller, but a Credential row is user data:
    a hand-edited or half-migrated value must degrade (no account-id header,
    per openai_chatgpt_params quirk 8) rather than take the adapter down."""
    adapter = ChatGptSubscriptionAdapter(base_url="https://x", api_key=api_key)
    assert adapter._access_token == api_key
    assert adapter._account_id is None


def test_headers_include_chatgpt_account_id_and_originator() -> None:
    adapter = ChatGptSubscriptionAdapter(
        base_url="https://x",
        api_key=json.dumps({"access_token": "at1", "account_id": "acct1"}),
    )
    headers = adapter._headers()
    assert headers["Authorization"] == "Bearer at1"
    assert headers["ChatGPT-Account-ID"] == "acct1"
    assert headers["originator"] == "codex_cli_rs"


def test_headers_omit_account_id_when_absent() -> None:
    adapter = ChatGptSubscriptionAdapter(base_url="https://x", api_key="plain-token")
    assert "ChatGPT-Account-ID" not in adapter._headers()


# --------------------------------------------------------------------------
# request building
# --------------------------------------------------------------------------


def test_payload_uses_typed_input_items_not_chat_messages() -> None:
    """`input` is a flat array of typed items, and a message's content is a
    list of typed parts -- `input_text` for what goes IN, `output_text` for a
    previous assistant turn. That asymmetry is the part a Chat-Completions
    habit gets wrong."""
    payload = build_responses_payload(
        _req(
            messages=[
                NeutralMessage(role="user", content="hallo"),
                NeutralMessage(role="assistant", content="hi"),
            ]
        )
    )
    assert payload["input"] == [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hallo"}]},
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hi"}],
        },
    ]


def test_system_messages_become_instructions_not_input_items() -> None:
    payload = build_responses_payload(
        _req(
            messages=[
                NeutralMessage(role="system", content="Du bist Nora."),
                NeutralMessage(role="system", content="Sei knapp."),
                NeutralMessage(role="user", content="hallo"),
            ]
        )
    )
    assert payload["instructions"] == "Du bist Nora.\n\nSei knapp."
    assert [item["role"] for item in payload["input"]] == ["user"]


def test_instructions_is_omitted_when_there_is_no_system_message() -> None:
    assert "instructions" not in build_responses_payload(_req())


def test_a_prior_tool_call_and_its_result_become_two_flat_items() -> None:
    """The Responses API does NOT nest tool calls inside an assistant message
    the way Chat Completions does: a call is its own `function_call` item and
    its result is its own `function_call_output` item, paired by `call_id`."""
    payload = build_responses_payload(
        _req(
            messages=[
                NeutralMessage(role="user", content="such mal"),
                NeutralMessage(
                    role="assistant",
                    tool_calls=[ToolCall(id="call_1", name="search", arguments={"q": "x"})],
                ),
                NeutralMessage(role="tool", content="3 Treffer", tool_call_id="call_1"),
            ]
        )
    )
    assert payload["input"][1] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "search",
        "arguments": '{"q": "x"}',
    }
    assert payload["input"][2] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "3 Treffer",
    }


def test_an_assistant_turn_with_both_text_and_a_call_emits_both_items() -> None:
    payload = build_responses_payload(
        _req(
            messages=[
                NeutralMessage(
                    role="assistant",
                    content="ich schaue nach",
                    tool_calls=[ToolCall(id="c1", name="search", arguments={})],
                ),
            ]
        )
    )
    assert [item["type"] for item in payload["input"]] == ["message", "function_call"]


def test_a_nameless_tool_call_and_its_orphaned_result_never_reach_the_provider() -> None:
    """Same permanent-poisoning failure the Chat-Completions path already
    guards (see test_openai_adapters.py): a call with no name is replayed on
    every later turn, so the run fails identically forever."""
    payload = build_responses_payload(
        _req(
            messages=[
                NeutralMessage(
                    role="assistant",
                    tool_calls=[
                        ToolCall(id="ok", name="search", arguments={}),
                        ToolCall(id="bad", name="", arguments={}),
                    ],
                ),
                NeutralMessage(role="tool", content="found", tool_call_id="ok"),
                NeutralMessage(role="tool", content="orphan", tool_call_id="bad"),
            ]
        )
    )
    calls = [i for i in payload["input"] if i["type"] == "function_call"]
    outputs = [i for i in payload["input"] if i["type"] == "function_call_output"]
    assert [c["call_id"] for c in calls] == ["ok"]
    assert [o["call_id"] for o in outputs] == ["ok"]


def test_tools_are_flat_function_objects_not_chat_completions_wrappers() -> None:
    """Chat Completions nests the function under a `function` key; the
    Responses API does not -- name/description/parameters sit directly on the
    tool object, and `strict` is a required field."""
    payload = build_responses_payload(
        _req(
            tools=[
                NeutralTool(
                    name="ping",
                    description="ping it",
                    parameters={"type": "object", "properties": {}},
                )
            ]
        )
    )
    assert payload["tools"] == [
        {
            "type": "function",
            "name": "ping",
            "description": "ping it",
            "strict": False,
            "parameters": {"type": "object", "properties": {}},
        }
    ]
    assert payload["tool_choice"] == "auto"


def test_tools_and_tool_choice_are_omitted_when_there_are_none() -> None:
    payload = build_responses_payload(_req())
    assert "tools" not in payload
    assert "tool_choice" not in payload


def test_the_request_never_asks_the_backend_to_store_the_response() -> None:
    assert build_responses_payload(_req())["store"] is False


def test_sampling_params_are_deliberately_not_forwarded() -> None:
    """`temperature` is rejected outright by the reasoning models this backend
    serves, and `max_output_tokens` counts invisible reasoning tokens, so a
    1024 default would return an empty `incomplete` answer. Codex sends
    neither -- see the module docstring."""
    payload = build_responses_payload(_req(params=ModelParams(temperature=0.9, max_tokens=64)))
    assert "temperature" not in payload
    assert "max_output_tokens" not in payload


# --------------------------------------------------------------------------
# response parsing
# --------------------------------------------------------------------------

#: Trimmed verbatim from the `Response` example in openai/openai-openapi
#: `openapi.yaml` (schema `Response`, `example:`).
_TEXT_RESPONSE: dict[str, Any] = {
    "id": "resp_67ccd3a9da748190baa7f1570fe91ac604becb25c45c1d41",
    "object": "response",
    "created_at": 1741476777,
    "status": "completed",
    "error": None,
    "incomplete_details": None,
    "model": "gpt-5-codex",
    "output": [
        {
            "type": "message",
            "id": "msg_67ccd3acc8d48190a77525dc6de64b41",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Hallo!", "annotations": []}],
        }
    ],
    "usage": {
        "input_tokens": 328,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "output_tokens": 52,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 380,
    },
}

_TOOL_CALL_RESPONSE: dict[str, Any] = {
    "id": "resp_2",
    "object": "response",
    "status": "completed",
    "model": "gpt-5-codex",
    "output": [
        {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [],
            "encrypted_content": "…",
        },
        {
            "type": "function_call",
            "id": "fc_680",
            "call_id": "call_abc",
            "name": "search",
            "arguments": '{"q": "wetter"}',
            "status": "completed",
        },
    ],
    "usage": {
        "input_tokens": 10,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "output_tokens": 5,
        "output_tokens_details": {"reasoning_tokens": 3},
        "total_tokens": 15,
    },
}


def test_parse_reads_text_out_of_output_message_content_parts() -> None:
    result = parse_responses_result(_TEXT_RESPONSE, provider="openai_chatgpt", model="gpt-5-codex")
    assert result.text == "Hallo!"
    assert result.tool_calls == []
    assert result.stop_reason == "stop"
    assert result.provider == "openai_chatgpt"
    assert result.model == "gpt-5-codex"


def test_parse_reads_usage_from_input_tokens_and_output_tokens() -> None:
    """Not `prompt_tokens`/`completion_tokens`: reading the Chat-Completions
    key names here would meter every subscription-backed run at zero and make
    the §15.4 budget blind."""
    usage = parse_responses_result(
        _TEXT_RESPONSE, provider="openai_chatgpt", model="gpt-5-codex"
    ).usage
    assert (usage.tokens_in, usage.tokens_out) == (328, 52)


def test_parse_reads_a_function_call_and_keys_it_by_call_id() -> None:
    """`call_id` -- not the item's own `id` -- is what a later
    `function_call_output` must quote, so that is the id the neutral ToolCall
    has to carry."""
    result = parse_responses_result(
        _TOOL_CALL_RESPONSE, provider="openai_chatgpt", model="gpt-5-codex"
    )
    assert result.tool_calls == [ToolCall(id="call_abc", name="search", arguments={"q": "wetter"})]
    assert result.stop_reason == "tool_use"
    assert result.text == ""


def test_parse_survives_unparseable_tool_arguments() -> None:
    data = {
        "output": [{"type": "function_call", "call_id": "c", "name": "n", "arguments": "{oops"}]
    }
    result = parse_responses_result(data, provider="openai_chatgpt", model="m")
    assert result.tool_calls == [ToolCall(id="c", name="n", arguments={})]


def test_parse_survives_a_response_with_nothing_in_it() -> None:
    result = parse_responses_result({}, provider="openai_chatgpt", model="m")
    assert (result.text, result.tool_calls, result.usage.tokens_in) == ("", [], 0)


# --------------------------------------------------------------------------
# complete() over HTTP
# --------------------------------------------------------------------------


async def test_complete_posts_to_the_responses_path_with_the_codex_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_post(
        self: httpx.AsyncClient, url: str, json: Any = None, **kw: Any
    ) -> httpx.Response:
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = kw.get("headers")
        return httpx.Response(200, json=_TEXT_RESPONSE, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    adapter = ChatGptSubscriptionAdapter(
        base_url="https://chatgpt.com/backend-api/codex",
        api_key=json.dumps({"access_token": "at1", "account_id": "acct1"}),
    )
    result = await adapter.complete(_req())

    assert captured["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert captured["json"]["model"] == "gpt-5-codex"
    assert captured["json"]["stream"] is False
    assert captured["headers"]["Authorization"] == "Bearer at1"
    assert captured["headers"]["ChatGPT-Account-ID"] == "acct1"
    assert captured["headers"]["originator"] == "codex_cli_rs"
    assert result.text == "Hallo!"
    assert result.provider == "openai_chatgpt"


async def test_complete_keeps_the_upstream_body_on_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post(
        self: httpx.AsyncClient, url: str, json: Any = None, **kw: Any
    ) -> httpx.Response:
        return httpx.Response(
            401,
            json={"detail": "Missing ChatGPT-Account-ID"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    adapter = ChatGptSubscriptionAdapter(base_url="https://x", api_key="t")
    with pytest.raises(httpx.HTTPStatusError, match="Missing ChatGPT-Account-ID"):
        await adapter.complete(_req())


# --------------------------------------------------------------------------
# stream()
# --------------------------------------------------------------------------


def _sse(*events: dict[str, Any]) -> bytes:
    """The Responses API frames each event as `event: <type>` + `data: <json>`
    and ends after `response.completed` -- there is no `[DONE]` sentinel."""
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _client(body: bytes, *, capture: dict[str, Any] | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["payload"] = json.loads(request.content)
            capture["headers"] = dict(request.headers)
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_stream_yields_text_deltas_in_order() -> None:
    body = _sse(
        {"type": "response.created", "response": {"id": "r1"}},
        {"type": "response.output_text.delta", "item_id": "m1", "output_index": 0, "delta": "Hal"},
        {"type": "response.output_text.delta", "item_id": "m1", "output_index": 0, "delta": "lo"},
        {"type": "response.output_text.done", "item_id": "m1", "text": "Hallo"},
        {
            "type": "response.completed",
            "response": {"id": "r1", "usage": {"input_tokens": 7, "output_tokens": 2}},
        },
    )
    async with _client(body) as c:
        chunks = [ch async for ch in stream_responses_sse("http://x/responses", {}, {}, client=c)]
    assert "".join(ch.text for ch in chunks) == "Hallo"
    assert chunks[-1].stop_reason == "stop"
    assert chunks[-1].usage is not None
    assert (chunks[-1].usage.tokens_in, chunks[-1].usage.tokens_out) == (7, 2)


async def test_stream_takes_a_tool_call_whole_from_output_item_done() -> None:
    """`response.function_call_arguments.delta` carries neither `call_id` nor
    `name`, so fragments alone cannot be turned into a dispatchable call.
    Codex ignores those deltas and reads the finished `function_call` item off
    `response.output_item.done` -- so does this."""
    body = _sse(
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "type": "function_call",
                "call_id": "call_1",
                "name": "search",
                "arguments": "",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "output_index": 0,
            "delta": '{"q":',
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "output_index": 0,
            "delta": '"x"}',
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "search",
                "arguments": '{"q":"x"}',
            },
        },
        {
            "type": "response.completed",
            "response": {"id": "r1", "usage": {"input_tokens": 3, "output_tokens": 4}},
        },
    )
    async with _client(body) as c:
        chunks = [ch async for ch in stream_responses_sse("http://x/responses", {}, {}, client=c)]

    deltas = [d for ch in chunks for d in ch.tool_calls]
    assert len(deltas) == 1, "the argument fragments must not be forwarded twice"
    assert deltas[0].id == "call_1"
    assert deltas[0].name == "search"
    assert json.loads(deltas[0].arguments_fragment) == {"q": "x"}
    assert chunks[-1].stop_reason == "tool_use"


async def test_stream_asks_for_a_stream() -> None:
    capture: dict[str, Any] = {}
    body = _sse({"type": "response.completed", "response": {"id": "r"}})
    async with _client(body, capture=capture) as c:
        _ = [
            ch
            async for ch in stream_responses_sse("http://x/responses", {"model": "m"}, {}, client=c)
        ]
    assert capture["payload"] == {"model": "m", "stream": True}


async def test_stream_raises_on_response_failed() -> None:
    body = _sse(
        {
            "type": "response.failed",
            "response": {
                "id": "r",
                "error": {"code": "rate_limit_exceeded", "message": "slow down"},
            },
        }
    )
    with pytest.raises(RuntimeError, match="slow down"):
        async with _client(body) as c:
            _ = [ch async for ch in stream_responses_sse("http://x/responses", {}, {}, client=c)]


async def test_stream_skips_events_it_does_not_understand() -> None:
    body = (
        b": keepalive\n\n"
        + _sse(
            {"type": "response.in_progress", "response": {"id": "r"}},
            {"type": "response.reasoning_summary_text.delta", "delta": "denke nach"},
            {"type": "response.output_text.delta", "delta": "ok"},
            {"type": "response.completed", "response": {"id": "r"}},
        )
        + b"data: not json\n\n"
    )
    async with _client(body) as c:
        chunks = [ch async for ch in stream_responses_sse("http://x/responses", {}, {}, client=c)]
    assert "".join(ch.text for ch in chunks) == "ok"


async def test_adapter_stream_hits_the_responses_path(monkeypatch: pytest.MonkeyPatch) -> None:
    capture: dict[str, Any] = {}
    body = _sse(
        {"type": "response.output_text.delta", "delta": "hi"},
        {
            "type": "response.completed",
            "response": {"id": "r", "usage": {"input_tokens": 1, "output_tokens": 1}},
        },
    )
    mock = httpx.MockTransport(
        lambda request: (
            capture.update(url=str(request.url), headers=dict(request.headers)),
            httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}),
        )[1]
    )
    real_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, **kw: Any) -> None:
        kw["transport"] = mock
        real_init(self, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    adapter = ChatGptSubscriptionAdapter(
        base_url="https://chatgpt.com/backend-api/codex/", api_key="tok"
    )
    chunks = [ch async for ch in adapter.stream(_req())]
    assert capture["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert capture["headers"]["authorization"] == "Bearer tok"
    assert "".join(ch.text for ch in chunks) == "hi"


# --------------------------------------------------------------------------
# usage: unknown must stay unknown (final-review Important #4)
# --------------------------------------------------------------------------


async def test_stream_reports_unknown_usage_as_none_not_zero() -> None:
    """`CompletionChunk.usage` is documented as staying None when the provider
    never reports usage, and `llm_gateway` relies on that: it does
    `reported or Usage(..., estimate_tokens(...))`, and a `Usage(0, 0)` is a
    truthy dataclass that defeats the estimate -- billing a whole streamed run
    at nothing. `response.completed` with no `usage` used to build exactly
    that zero.
    """
    body = _sse({"type": "response.completed", "response": {"id": "r"}})
    async with _client(body) as c:
        chunks = [
            ch
            async for ch in stream_responses_sse("http://x/responses", {"model": "m"}, {}, client=c)
        ]
    assert chunks[-1].stop_reason == "stop"
    assert chunks[-1].usage is None


async def test_stream_reports_malformed_usage_as_none_not_zero() -> None:
    """Same rule when `usage` is present but is not an object at all."""
    body = _sse({"type": "response.completed", "response": {"id": "r", "usage": "nonsense"}})
    async with _client(body) as c:
        chunks = [
            ch
            async for ch in stream_responses_sse("http://x/responses", {"model": "m"}, {}, client=c)
        ]
    assert chunks[-1].usage is None


async def test_stream_reports_a_non_dict_response_envelope_as_none_usage() -> None:
    body = _sse({"type": "response.completed", "response": "nonsense"})
    async with _client(body) as c:
        chunks = [
            ch
            async for ch in stream_responses_sse("http://x/responses", {"model": "m"}, {}, client=c)
        ]
    assert chunks[-1].usage is None


async def test_stream_still_reports_a_genuine_zero_usage() -> None:
    """A real, present `{"input_tokens": 0, "output_tokens": 0}` is data, not
    absence -- it must survive as Usage(0, 0), the same way
    `streaming._chunk_from_openai` keeps it."""
    body = _sse(
        {
            "type": "response.completed",
            "response": {"id": "r", "usage": {"input_tokens": 0, "output_tokens": 0}},
        }
    )
    async with _client(body) as c:
        chunks = [
            ch
            async for ch in stream_responses_sse("http://x/responses", {"model": "m"}, {}, client=c)
        ]
    assert chunks[-1].usage is not None
    assert (chunks[-1].usage.tokens_in, chunks[-1].usage.tokens_out) == (0, 0)


def test_parse_still_reports_zero_usage_for_a_usage_less_response() -> None:
    """The NON-streaming path is unchanged: `CompletionResult.usage` is not
    optional, and its caller holds the whole request to price itself."""
    result = parse_responses_result({"output": []}, provider="openai_chatgpt", model="m")
    assert (result.usage.tokens_in, result.usage.tokens_out) == (0, 0)


# --------------------------------------------------------------------------
# composite api_key parsing (final-review M2)
# --------------------------------------------------------------------------


def test_a_null_access_token_never_ships_as_bearer_none() -> None:
    """`str(None)` is the literal "None", which used to go out as
    `Authorization: Bearer None`. An absent token means no header at all."""
    adapter = ChatGptSubscriptionAdapter(
        "http://x", json.dumps({"access_token": None, "account_id": "acct_1"})
    )
    headers = adapter._headers()
    assert "Authorization" not in headers
    assert headers["ChatGPT-Account-ID"] == "acct_1"


def test_an_empty_access_token_also_sends_no_authorization_header() -> None:
    adapter = ChatGptSubscriptionAdapter("http://x", json.dumps({"access_token": ""}))
    assert "Authorization" not in adapter._headers()


def test_a_real_access_token_still_becomes_a_bearer_header() -> None:
    adapter = ChatGptSubscriptionAdapter(
        "http://x", json.dumps({"access_token": "tok", "account_id": "acct_1"})
    )
    assert adapter._headers()["Authorization"] == "Bearer tok"
