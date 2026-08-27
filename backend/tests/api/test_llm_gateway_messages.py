"""The Anthropic Messages surface of the gateway (§8.7 R1).

This is the endpoint nanoclaw / the Claude Agent SDK actually points at via
ANTHROPIC_BASE_URL. It is NOT an alias of the OpenAI one: `system` is a top-level
field rather than a message, tool results ride inside USER messages as blocks, and
the streaming event vocabulary is different. Getting the mapping wrong loses the
agent's instructions or its tool results silently -- the run would just behave
stupidly rather than fail.
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
from oc8.modelrouter.types import CompletionChunk, CompletionResult, ToolCall, Usage
from oc8.runtime.states import RunState
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

MESSAGES = "/llm/v1/messages"


async def _agent_run(db: Any, tenant: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    dept = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
    db.add(dept)
    await db.flush()
    cfg = m.ModelConfig(
        tenant_id=tenant, provider="openai_compatible", model="opaas_ai:odoo-gpt", params={}
    )
    db.add(cfg)
    await db.flush()
    agent = m.Agent(
        tenant_id=tenant, department_id=dept.id, name="Nora", status="running",
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
            r = await c.post(MESSAGES, json=body, headers={"Authorization": f"Bearer {token}"})
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


async def test_the_top_level_system_prompt_is_not_lost(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In this API `system` is a FIELD, not a message. Dropping it would strip the
    agent's entire instruction set and the run would just behave badly, with
    nothing in any log to say why."""
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
            "model": "claude-sonnet-4-5",
            "max_tokens": 256,
            "system": "Du bist Nora.",
            "messages": [{"role": "user", "content": "hallo"}],
        },
    )
    assert code == 200, body
    msgs = seen["messages"]
    assert msgs[0].role == "system"
    assert msgs[0].content == "Du bist Nora."
    assert msgs[1].role == "user"


async def test_a_system_prompt_given_as_blocks_is_joined(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SDK sends `system` as a list of text blocks when it uses caching."""
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
            "model": "c", "max_tokens": 16,
            "system": [
                {"type": "text", "text": "Teil eins."},
                {"type": "text", "text": "Teil zwei."},
            ],
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert code == 200, body
    assert "Teil eins." in seen["messages"][0].content
    assert "Teil zwei." in seen["messages"][0].content


async def test_a_tool_result_block_becomes_a_tool_message(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tool results arrive inside a USER message here, not as their own role.
    Treating them as ordinary user text would hide from the model that its call
    ever ran, and it would call the tool again."""
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
            "model": "c", "max_tokens": 16,
            "messages": [
                {"role": "user", "content": "leg was an"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "mache ich"},
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "create_record",
                            "input": {"model": "sale.order"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok, id=7"}
                    ],
                },
            ],
        },
    )
    assert code == 200, body
    msgs = seen["messages"]
    assistant = next(x for x in msgs if x.role == "assistant")
    assert assistant.tool_calls[0].id == "toolu_1"
    assert assistant.tool_calls[0].name == "create_record"
    assert assistant.tool_calls[0].arguments == {"model": "sale.order"}
    tool_msg = next(x for x in msgs if x.role == "tool")
    assert tool_msg.tool_call_id == "toolu_1"
    assert "id=7" in tool_msg.content


async def test_tools_use_input_schema_not_parameters(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This API names the schema `input_schema`. Reading `parameters` would give
    every tool an empty schema and the model would call them with nothing."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, run_id, _d = await _agent_run(db, tenant)

    seen: dict[str, Any] = {}

    async def fake_complete(*a: object, **kw: object) -> CompletionResult:
        seen["tools"] = kw.get("tools")
        return _result()

    monkeypatch.setattr("oc8.api.llm_gateway.complete_with_fallback", fake_complete)

    schema = {"type": "object", "properties": {"model": {"type": "string"}}, "required": ["model"]}
    code, body = await _post(
        _token(tenant, agent_id, run_id),
        {
            "model": "c", "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {"name": "create_record", "description": "create it", "input_schema": schema}
            ],
        },
    )
    assert code == 200, body
    tool = seen["tools"][0]
    assert tool.name == "create_record"
    assert tool.parameters == schema


# ----------------------------------------------------------- outbound mapping


async def test_the_response_uses_content_blocks_and_anthropic_usage_names(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, run_id, _d = await _agent_run(db, tenant)

    monkeypatch.setattr(
        "oc8.api.llm_gateway.complete_with_fallback",
        lambda *a, **kw: _async(_result(text="fertig")),
    )

    code, body = await _post(
        _token(tenant, agent_id, run_id),
        {"model": "c", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert code == 200, body
    assert body["type"] == "message"
    assert body["role"] == "assistant"
    assert body["content"] == [{"type": "text", "text": "fertig"}]
    assert body["stop_reason"] == "end_turn"
    # input_tokens/output_tokens, NOT prompt_tokens/completion_tokens.
    assert body["usage"] == {"input_tokens": 120, "output_tokens": 30}
    assert body["model"] == "opaas_ai:odoo-gpt", "the resolved model is echoed"


async def test_a_tool_call_becomes_a_tool_use_block_and_stop_reason(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A harness decides whether to run a tool from stop_reason == 'tool_use'. Any
    other value and it would stop instead of acting."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, run_id, _d = await _agent_run(db, tenant)

    monkeypatch.setattr(
        "oc8.api.llm_gateway.complete_with_fallback",
        lambda *a, **kw: _async(
            _result(
                text="",
                tool_calls=[ToolCall(id="toolu_9", name="create_record", arguments={"m": 1})],
                stop_reason="tool_use",
            )
        ),
    )

    code, body = await _post(
        _token(tenant, agent_id, run_id),
        {"model": "c", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert code == 200, body
    assert body["stop_reason"] == "tool_use"
    block = next(b for b in body["content"] if b["type"] == "tool_use")
    assert block["id"] == "toolu_9"
    assert block["name"] == "create_record"
    assert block["input"] == {"m": 1}


async def test_a_nameless_tool_call_never_enters_the_transcript(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The harness stores whatever we hand it and replays it forever.

    Live, 2026-07-27: the model produced one tool call with an empty name. We
    passed it through as a tool_use block, the harness wrote it into its
    transcript, and every later turn sent it back -- 400 "Function name was  but
    must be a-z…" on each one, the run dead, no retry able to help. Refusing to
    emit it is what keeps a session from being poisoned in the first place; the
    outbound translation drops such calls too, which is what rescues a session
    already carrying one.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, run_id, _d = await _agent_run(db, tenant)

    monkeypatch.setattr(
        "oc8.api.llm_gateway.complete_with_fallback",
        lambda *a, **kw: _async(
            _result(
                text="Ich sehe nach.",
                tool_calls=[
                    ToolCall(id="toolu_bad", name="", arguments={}),
                    ToolCall(id="toolu_ok", name="search_records", arguments={"m": 1}),
                ],
                stop_reason="tool_use",
            )
        ),
    )

    code, body = await _post(
        _token(tenant, agent_id, run_id),
        {"model": "c", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert code == 200, body
    blocks = [b for b in body["content"] if b["type"] == "tool_use"]
    assert [b["name"] for b in blocks] == ["search_records"]
    # The turn still had a real call, so the harness must still be told to act.
    assert body["stop_reason"] == "tool_use"


async def _async(value: Any) -> Any:
    return value


# --------------------------------------------------------------- streaming


async def test_streaming_emits_the_anthropic_event_sequence(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A harness drives its state machine off these event names. The order and the
    presence of message_start / message_stop is the contract."""
    from sqlalchemy import select

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
                "POST", MESSAGES,
                json={"model": "c", "max_tokens": 16, "stream": True,
                      "messages": [{"role": "user", "content": "hi"}]},
                headers={"Authorization": f"Bearer {_token(tenant, agent_id, run_id)}"},
            ) as r:
                assert r.status_code == 200
                assert r.headers["content-type"].startswith("text/event-stream")
                lines = [ln async for ln in r.aiter_lines()]

    events = [ln[len("event:"):].strip() for ln in lines if ln.startswith("event:")]
    assert events[0] == "message_start"
    assert "content_block_start" in events
    assert "content_block_delta" in events
    assert events[-1] == "message_stop"
    assert "message_delta" in events

    datas = [json.loads(ln[len("data:"):]) for ln in lines if ln.startswith("data:")]
    text = "".join(
        d["delta"]["text"]
        for d in datas
        if d.get("type") == "content_block_delta" and d["delta"].get("type") == "text_delta"
    )
    assert text == "Hallo"

    async with app_session(tenant) as db:
        row = (
            await db.execute(
                select(m.TokenUsageRecord).where(m.TokenUsageRecord.tenant_id == tenant)
            )
        ).scalar_one()
        assert row.tokens_in == 11 and row.tokens_out == 2


async def test_streamed_tool_calls_use_input_json_delta(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Arguments stream as partial_json under input_json_delta here, not as an
    OpenAI arguments fragment. A harness reassembles from exactly this."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, run_id, _d = await _agent_run(db, tenant)

    from oc8.modelrouter.types import ToolCallDelta

    async def fake_stream(*a: object, **kw: object) -> AsyncIterator[CompletionChunk]:
        yield CompletionChunk(
            tool_calls=[ToolCallDelta(index=0, id="toolu_1", name="create_record",
                                      arguments_fragment='{"mo')]
        )
        yield CompletionChunk(tool_calls=[ToolCallDelta(index=0, arguments_fragment='del":1}')])
        yield CompletionChunk(stop_reason="tool_use", usage=Usage(1, 1))

    monkeypatch.setattr("oc8.api.llm_gateway.stream_completion_with_fallback", fake_stream)

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            async with c.stream(
                "POST", MESSAGES,
                json={"model": "c", "max_tokens": 16, "stream": True,
                      "messages": [{"role": "user", "content": "hi"}]},
                headers={"Authorization": f"Bearer {_token(tenant, agent_id, run_id)}"},
            ) as r:
                lines = [ln async for ln in r.aiter_lines()]

    datas = [json.loads(ln[len("data:"):]) for ln in lines if ln.startswith("data:")]
    starts = [d for d in datas if d.get("type") == "content_block_start"]
    tool_start = next(d for d in starts if d["content_block"]["type"] == "tool_use")
    assert tool_start["content_block"]["id"] == "toolu_1"
    assert tool_start["content_block"]["name"] == "create_record"
    frags = "".join(
        d["delta"]["partial_json"]
        for d in datas
        if d.get("type") == "content_block_delta" and d["delta"].get("type") == "input_json_delta"
    )
    assert frags == '{"model":1}'
    stop = next(d for d in datas if d.get("type") == "message_delta")
    assert stop["delta"]["stop_reason"] == "tool_use"


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
        {"model": "c", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert code == 429, body


async def test_the_models_endpoint_lists_what_the_tenant_may_use(
    app_session: AppSessionFactory,
) -> None:
    """A harness probes this on startup; an empty or failing list makes it give up
    before the first turn."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, run_id, _d = await _agent_run(db, tenant)

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get(
                "/llm/v1/models",
                headers={"Authorization": f"Bearer {_token(tenant, agent_id, run_id)}"},
            )
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [d["id"] for d in body["data"]]
    assert "opaas_ai:odoo-gpt" in ids, ids
