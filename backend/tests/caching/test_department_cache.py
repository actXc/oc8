# backend/tests/caching/test_department_cache.py
from __future__ import annotations

import uuid

import pytest

from oc8 import models as m
from oc8.caching.department_cache import cache_key, get_cached, is_enabled, store
from oc8.modelrouter.types import (
    CompletionResult,
    ModelParams,
    NeutralMessage,
    ToolCall,
    Usage,
)

pytestmark = pytest.mark.asyncio


def _dept(*, enabled: bool = True) -> m.Department:
    return m.Department(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="Sales",
        goal="",
        frame={},
        presentation={},
        prompt_caching_enabled=enabled,
    )


def _messages() -> list[NeutralMessage]:
    return [NeutralMessage(role="user", content="classify this ticket")]


def test_is_enabled_reads_the_department_flag() -> None:
    assert is_enabled(_dept(enabled=True)) is True
    assert is_enabled(_dept(enabled=False)) is False


def test_cache_key_is_deterministic_for_identical_input() -> None:
    tenant, dept = uuid.uuid4(), uuid.uuid4()
    k1 = cache_key(
        tenant_id=tenant,
        department_id=dept,
        provider="anthropic",
        model="claude-sonnet-4",
        messages=_messages(),
        tools=[],
        params=ModelParams(),
    )
    k2 = cache_key(
        tenant_id=tenant,
        department_id=dept,
        provider="anthropic",
        model="claude-sonnet-4",
        messages=_messages(),
        tools=[],
        params=ModelParams(),
    )
    assert k1 == k2


def test_cache_key_changes_with_messages() -> None:
    tenant, dept = uuid.uuid4(), uuid.uuid4()
    k1 = cache_key(
        tenant_id=tenant,
        department_id=dept,
        provider="anthropic",
        model="claude-sonnet-4",
        messages=[NeutralMessage(role="user", content="question A")],
        tools=[],
        params=ModelParams(),
    )
    k2 = cache_key(
        tenant_id=tenant,
        department_id=dept,
        provider="anthropic",
        model="claude-sonnet-4",
        messages=[NeutralMessage(role="user", content="question B")],
        tools=[],
        params=ModelParams(),
    )
    assert k1 != k2


def test_cache_key_changes_with_provider_even_if_model_name_matches() -> None:
    tenant, dept = uuid.uuid4(), uuid.uuid4()
    k1 = cache_key(
        tenant_id=tenant,
        department_id=dept,
        provider="anthropic",
        model="llama3",
        messages=_messages(),
        tools=[],
        params=ModelParams(),
    )
    k2 = cache_key(
        tenant_id=tenant,
        department_id=dept,
        provider="ollama",
        model="llama3",
        messages=_messages(),
        tools=[],
        params=ModelParams(),
    )
    assert k1 != k2


def test_cache_key_changes_with_base_url_even_if_provider_and_model_match() -> None:
    """`base_url` selects which server actually answers. Two ModelConfigs in
    one department can share a provider AND a model string while pointing at
    different endpoints (a cloud one and a self-hosted OpenAI-compatible one);
    they must not collide on one entry."""
    tenant, dept = uuid.uuid4(), uuid.uuid4()
    common = {
        "tenant_id": tenant,
        "department_id": dept,
        "provider": "openai_compatible",
        "model": "gpt-4o-mini",
        "messages": _messages(),
        "tools": [],
        "params": ModelParams(),
    }
    k1 = cache_key(**common, base_url="https://api.openai.com/v1")  # type: ignore[arg-type]
    k2 = cache_key(**common, base_url="https://llm.internal.example/v1")  # type: ignore[arg-type]
    k3 = cache_key(**common)  # type: ignore[arg-type]
    assert k1 != k2
    assert k1 != k3 and k2 != k3


def test_cache_key_is_namespaced_by_department_even_for_identical_content() -> None:
    tenant = uuid.uuid4()
    dept_a, dept_b = uuid.uuid4(), uuid.uuid4()
    k1 = cache_key(
        tenant_id=tenant,
        department_id=dept_a,
        provider="anthropic",
        model="claude-sonnet-4",
        messages=_messages(),
        tools=[],
        params=ModelParams(),
    )
    k2 = cache_key(
        tenant_id=tenant,
        department_id=dept_b,
        provider="anthropic",
        model="claude-sonnet-4",
        messages=_messages(),
        tools=[],
        params=ModelParams(),
    )
    assert k1 != k2
    # And it's not just a different hash -- the department id must appear as a
    # literal, distinct namespace segment, so a hash-collision bug could never
    # bridge the two even in principle.
    assert str(dept_a) in k1 and str(dept_a) not in k2
    assert str(dept_b) in k2 and str(dept_b) not in k1


async def test_get_cached_returns_none_for_a_key_never_stored(redis_url: str) -> None:
    result = await get_cached("deptcache:nonexistent:nonexistent:nonexistent")
    assert result is None


async def test_store_then_get_cached_round_trips_the_result(redis_url: str) -> None:
    tenant, dept = uuid.uuid4(), uuid.uuid4()
    key = cache_key(
        tenant_id=tenant,
        department_id=dept,
        provider="anthropic",
        model="claude-sonnet-4",
        messages=_messages(),
        tools=[],
        params=ModelParams(),
    )
    original = CompletionResult(
        text="This is a triage response.",
        tool_calls=[ToolCall(id="call_1", name="update_record", arguments={"id": "abc"})],
        usage=Usage(tokens_in=120, tokens_out=40),
        stop_reason="end_turn",
        provider="anthropic",
        model="claude-sonnet-4",
    )
    await store(key, original)
    got = await get_cached(key)
    assert got is not None
    assert got.text == original.text
    assert got.tool_calls == original.tool_calls
    assert got.usage == original.usage
    assert got.stop_reason == original.stop_reason
    assert got.provider == original.provider
    assert got.model == original.model


async def test_store_respects_ttl(redis_url: str) -> None:
    tenant, dept = uuid.uuid4(), uuid.uuid4()
    key = cache_key(
        tenant_id=tenant,
        department_id=dept,
        provider="anthropic",
        model="claude-sonnet-4",
        messages=_messages(),
        tools=[],
        params=ModelParams(),
    )
    result = CompletionResult(
        text="x",
        tool_calls=[],
        usage=Usage(tokens_in=1, tokens_out=1),
        stop_reason="end_turn",
        provider="anthropic",
        model="claude-sonnet-4",
    )
    await store(key, result, ttl_s=1)
    import asyncio

    await asyncio.sleep(1.5)
    assert await get_cached(key) is None
