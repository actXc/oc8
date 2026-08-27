"""The OpenAI Responses surface of the gateway (§8.7 R1).

This is the endpoint codex-cli points at via OPENAI_BASE_URL + wire_api=
"responses" (see plugins/codex_runtime/runtime/runtime.py's config.toml).
It is NOT an alias of /v1/chat/completions: input is a flat array of typed
ITEMS (message, function_call, function_call_output), not role/content
turns; the system prompt rides in a top-level `instructions` field, not the
first message; tool definitions are flat (`{"type":"function","name":...}`),
not nested under a `function` key; and the streaming event vocabulary is
its own. Getting the mapping wrong silently strips the agent's instructions
or loses a tool call -- the run behaves stupidly rather than failing.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from oc8.modelrouter.types import CompletionChunk, CompletionResult, ToolCall, ToolCallDelta, Usage
from oc8.runtime.states import RunState
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

RESPONSES = "/llm/v1/responses"


async def _agent_run(db: Any, tenant: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    dept = m.Department(tenant_id=tenant, name="Engineering", frame={})
    db.add(dept)
    await db.flush()
    cfg = m.ModelConfig(
        tenant_id=tenant, provider="openai_compatible", model="opaas_ai:odoo-gpt", params={}
    )
    db.add(cfg)
    await db.flush()
    agent = m.Agent(
        tenant_id=tenant, department_id=dept.id, name="Devon", status="running",
        narrowing={}, definition={}, presentation={}, model_config_id=cfg.id,
    )
    db.add(agent)
    await db.flush()
    run = m.AgentRun(
        tenant_id=tenant, agent_id=agent.id, state=RunState.RUNNING.value, context={"task": "x"}
    )
    db.add(run)
    await db.flush()
    return agent.id, run.id, dept.id


def _token(tenant: uuid.UUID, agent_id: uuid.UUID, run_id: uuid.UUID) -> str:
    return get_identity_provider().mint(
        tenant_id=tenant, subject=f"agent:{agent_id}", role="agent_default",
        kind="agent", scopes=[f"run:{run_id}"],
    )


async def _post(token: str, body: dict[str, Any]) -> tuple[int, Any]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(RESPONSES, json=body, headers={"Authorization": f"Bearer {token}"})
            return r.status_code, (r.json() if r.content else None)


def _result(**kw: Any) -> CompletionResult:
    return CompletionResult(
        text=kw.get("text", "fertig"),
        tool_calls=kw.get("tool_calls", []),
        usage=kw.get("usage", Usage(tokens_in=120, tokens_out=30)),
        stop_reason=kw.get("stop_reason", "stop"),
        provider="openai_compatible",
        model="opaas_ai:odoo-gpt",
    )


# ------------------------------------------------------------ inbound mapping


async def test_the_top_level_instructions_field_is_not_lost(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`instructions` is a top-level FIELD here, not the first input item.
    Dropping it would strip the agent's entire system prompt with nothing in
    any log to explain the resulting behaviour."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, run_id, _d = await _agent_run(db, tenant)

    seen: dict[str, Any] = {}

    async def fake_complete(*a: object, **kw: object) -> CompletionResult:
        seen["messages"] = kw.get("messages")
        return _result()

    monkeypatch.setattr("oc8.api.llm_gateway.complete_with_fallback", fake_complete)

    code, body = await _post(
        _token(tenant, agent_id, run_id),
        {
            "model": "oc8-gateway",
            "instructions": "You are Devon, a coding agent.",
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "hi"}
            ]}],
        },
    )
    assert code == 200, body
    msgs = seen["messages"]
    assert msgs[0].role == "system"
    assert msgs[0].content == "You are Devon, a coding agent."
    assert msgs[1].role == "user"
    assert msgs[1].content == "hi"


async def test_developer_role_input_items_fold_into_system(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex-cli sends its skills/permissions block as a "developer"-role
    message inside `input`, not via `instructions` -- observed live. Neutral
    has no fourth role, so this folds into "system" alongside `instructions`
    rather than being dropped or misread as a user turn."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, run_id, _d = await _agent_run(db, tenant)

    seen: dict[str, Any] = {}

    async def fake_complete(*a: object, **kw: object) -> CompletionResult:
        seen["messages"] = kw.get("messages")
        return _result()

    monkeypatch.setattr("oc8.api.llm_gateway.complete_with_fallback", fake_complete)

    code, body = await _post(
        _token(tenant, agent_id, run_id),
        {
            "model": "oc8-gateway",
            "input": [
                {"type": "message", "role": "developer", "content": [
                    {"type": "input_text", "text": "<permissions>...</permissions>"}
                ]},
                {"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "hi"}
                ]},
            ],
        },
    )
    assert code == 200, body
    msgs = seen["messages"]
    assert msgs[0].role == "system"
    assert msgs[0].content == "<permissions>...</permissions>"
    assert msgs[1].role == "user"


async def test_a_prior_function_call_and_its_output_round_trip(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a later turn codex replays its own earlier tool call
    (`function_call`, arguments as a JSON STRING) and the tool's result
    (`function_call_output`) as separate input items, not as Chat
    Completions' assistant.tool_calls + a following tool message. Both must
    map to the SAME neutral shape or the model loses track of what it
    already tried."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, run_id, _d = await _agent_run(db, tenant)

    seen: dict[str, Any] = {}

    async def fake_complete(*a: object, **kw: object) -> CompletionResult:
        seen["messages"] = kw.get("messages")
        return _result()

    monkeypatch.setattr("oc8.api.llm_gateway.complete_with_fallback", fake_complete)

    code, body = await _post(
        _token(tenant, agent_id, run_id),
        {
            "model": "oc8-gateway",
            "input": [
                {"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "who am I"}
                ]},
                {"type": "function_call", "call_id": "call_1", "name": "get_me",
                 "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_1",
                 "output": "oc8-dev"},
            ],
        },
    )
    assert code == 200, body
    msgs = seen["messages"]
    call_msg = next(m for m in msgs if m.tool_calls)
    assert call_msg.tool_calls[0].id == "call_1"
    assert call_msg.tool_calls[0].name == "get_me"
    tool_msg = next(m for m in msgs if m.role == "tool")
    assert tool_msg.tool_call_id == "call_1"
    assert tool_msg.content == "oc8-dev"


async def test_flat_tool_definitions_are_read_correctly(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tools arrive flat here (`{"type":"function","name":...,"parameters":...}`),
    unlike Chat Completions' `{"type":"function","function":{"name":...}}`
    nesting -- verified live against codex-cli's real traffic. Reading the
    wrong shape silently gives every tool an empty name and the model can
    never call one."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, run_id, _d = await _agent_run(db, tenant)

    seen: dict[str, Any] = {}

    async def fake_complete(*a: object, **kw: object) -> CompletionResult:
        seen["tools"] = kw.get("tools")
        return _result()

    monkeypatch.setattr("oc8.api.llm_gateway.complete_with_fallback", fake_complete)

    code, body = await _post(
        _token(tenant, agent_id, run_id),
        {
            "model": "oc8-gateway",
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "hi"}
            ]}],
            "tools": [
                {"type": "function", "name": "get_me", "description": "Who am I",
                 "parameters": {"type": "object", "properties": {}}}
            ],
        },
    )
    assert code == 200, body
    tools = seen["tools"]
    assert len(tools) == 1
    assert tools[0].name == "get_me"
    assert tools[0].description == "Who am I"


# ------------------------------------------------------------------ outbound


async def test_non_streaming_response_carries_text_and_tool_calls(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, run_id, _d = await _agent_run(db, tenant)

    async def fake_complete(*a: object, **kw: object) -> CompletionResult:
        return _result(
            text="here you go",
            tool_calls=[ToolCall(id="call_1", name="get_me", arguments={})],
        )

    monkeypatch.setattr("oc8.api.llm_gateway.complete_with_fallback", fake_complete)

    code, body = await _post(
        _token(tenant, agent_id, run_id),
        {
            "model": "oc8-gateway",
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "hi"}
            ]}],
        },
    )
    assert code == 200, body
    assert body["object"] == "response"
    assert body["status"] == "completed"
    message_item = next(o for o in body["output"] if o["type"] == "message")
    assert message_item["content"][0]["text"] == "here you go"
    call_item = next(o for o in body["output"] if o["type"] == "function_call")
    assert call_item["name"] == "get_me"
    assert call_item["call_id"] == "call_1"


# --------------------------------------------------------------- streaming


async def test_streaming_emits_the_responses_event_sequence(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex-cli always sends stream:true (verified live) -- this is the
    path that actually matters. response.created and response.completed
    must bracket every stream, mirroring message_start/message_stop's role
    in the Anthropic surface: without a terminal event a harness waits
    forever."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, run_id, _d = await _agent_run(db, tenant)

    async def fake_stream(*a: object, **kw: object) -> AsyncIterator[CompletionChunk]:
        yield CompletionChunk(text="Hal")
        yield CompletionChunk(text="lo")
        yield CompletionChunk(stop_reason="stop", usage=Usage(tokens_in=11, tokens_out=2))

    monkeypatch.setattr("oc8.api.llm_gateway.stream_completion_with_fallback", fake_stream)

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            async with c.stream(
                "POST", RESPONSES,
                json={"model": "oc8-gateway", "stream": True,
                      "input": [{"type": "message", "role": "user",
                                 "content": [{"type": "input_text", "text": "hi"}]}]},
                headers={"Authorization": f"Bearer {_token(tenant, agent_id, run_id)}"},
            ) as r:
                assert r.status_code == 200
                assert r.headers["content-type"].startswith("text/event-stream")
                lines = [ln async for ln in r.aiter_lines()]

    events = [ln[len("event:") :].strip() for ln in lines if ln.startswith("event:")]
    assert events[0] == "response.created"
    assert "response.output_item.added" in events
    assert "response.output_text.delta" in events
    assert "response.output_item.done" in events
    assert events[-1] == "response.completed"

    datas = [json.loads(ln[len("data:") :]) for ln in lines if ln.startswith("data:")]
    text = "".join(
        d["delta"] for d in datas if d.get("type") == "response.output_text.delta"
    )
    assert text == "Hallo"

    # Every event carries a monotonic sequence_number -- codex-rs is a
    # strict Rust parser, so this is asserted rather than left implicit.
    seqs = [d["sequence_number"] for d in datas]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)

    completed = next(d for d in datas if d.get("type") == "response.completed")
    assert completed["response"]["status"] == "completed"
    message_item = next(
        o for o in completed["response"]["output"] if o["type"] == "message"
    )
    assert message_item["content"][0]["text"] == "Hallo"

    from sqlalchemy import select

    async with app_session(tenant) as db:
        row = (
            await db.execute(
                select(m.TokenUsageRecord).where(m.TokenUsageRecord.tenant_id == tenant)
            )
        ).scalar_one()
        assert row.tokens_in == 11 and row.tokens_out == 2


async def test_streamed_tool_calls_use_function_call_arguments_delta(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Arguments stream as function_call_arguments.delta here, not as
    OpenAI's Chat Completions tool_calls[].function.arguments fragment or
    Anthropic's input_json_delta. A harness reassembles from exactly this
    event name."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, run_id, _d = await _agent_run(db, tenant)

    async def fake_stream(*a: object, **kw: object) -> AsyncIterator[CompletionChunk]:
        yield CompletionChunk(
            tool_calls=[ToolCallDelta(index=0, id="call_1", name="get_me",
                                       arguments_fragment="{}")]
        )
        yield CompletionChunk(stop_reason="tool_use", usage=Usage(1, 1))

    monkeypatch.setattr("oc8.api.llm_gateway.stream_completion_with_fallback", fake_stream)

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            async with c.stream(
                "POST", RESPONSES,
                json={"model": "oc8-gateway", "stream": True,
                      "input": [{"type": "message", "role": "user",
                                 "content": [{"type": "input_text", "text": "hi"}]}]},
                headers={"Authorization": f"Bearer {_token(tenant, agent_id, run_id)}"},
            ) as r:
                lines = [ln async for ln in r.aiter_lines()]

    datas = [json.loads(ln[len("data:") :]) for ln in lines if ln.startswith("data:")]
    added = next(d for d in datas if d.get("type") == "response.output_item.added")
    assert added["item"]["type"] == "function_call"
    assert added["item"]["call_id"] == "call_1"
    assert added["item"]["name"] == "get_me"

    deltas = [d for d in datas if d.get("type") == "response.function_call_arguments.delta"]
    assert "".join(d["delta"] for d in deltas) == "{}"

    done = next(d for d in datas if d.get("type") == "response.function_call_arguments.done")
    assert done["arguments"] == "{}"

    completed = next(d for d in datas if d.get("type") == "response.completed")
    call_item = next(
        o for o in completed["response"]["output"] if o["type"] == "function_call"
    )
    assert call_item["call_id"] == "call_1"
    assert call_item["arguments"] == "{}"


# ------------------------------------------------------------------- policy


async def test_the_budget_refusal_is_a_429_here_too(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, run_id, dept_id = await _agent_run(db, tenant)
        db.add(m.Budget(tenant_id=tenant, department_id=None, hard_limit_tokens=10))
        db.add(
            m.TokenUsageRecord(
                tenant_id=tenant, request_id=uuid.uuid4(), model="m", provider="p",
                tokens_in=100, tokens_out=100, agent_id=agent_id, department_id=dept_id,
            )
        )

    code, body = await _post(
        _token(tenant, agent_id, run_id),
        {
            "model": "oc8-gateway",
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "hi"}
            ]}],
        },
    )
    assert code == 429, body
