"""Department-scoped LLM response cache (§ department prompt caching).

Full-response deduplication, not provider-native prompt caching -- see
docs/superpowers/specs/2026-08-11-department-prompt-caching-design.md for why
these are different mechanisms and why this one is exact-match only, scoped
to a single department, never wider.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from dataclasses import asdict

import redis.asyncio as redis

from oc8 import models as m
from oc8.config import get_settings
from oc8.modelrouter.types import (
    CompletionResult,
    ModelParams,
    NeutralMessage,
    NeutralTool,
    ToolCall,
    Usage,
)

_DEFAULT_TTL_S = 86400  # 24h -- see design doc §4 for the reasoning


_SOCKET_TIMEOUT_S = 0.5  # sub-second: a hung/blackholed Redis must degrade to a
# miss as fast as a refused connection does, or a stalled department cache
# would stall every step of every agent run in that department. redis-py's
# asyncio client defaults socket_connect_timeout/socket_timeout to None (no
# timeout at all) -- get_cached/store's callers rely on this actually
# producing a timeout to catch, not just on a clean connection refusal.


def _client() -> redis.Redis:
    # Same construction as oc8.oauth.state._client() -- one client per call,
    # matching this codebase's existing convention for short-lived Redis use.
    return redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=_SOCKET_TIMEOUT_S,
        socket_timeout=_SOCKET_TIMEOUT_S,
    )


def is_enabled(department: m.Department) -> bool:
    return department.prompt_caching_enabled


def cache_key(
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID,
    provider: str,
    model: str,
    base_url: str | None = None,
    messages: list[NeutralMessage],
    tools: list[NeutralTool],
    params: ModelParams,
) -> str:
    """Redis key for this exact request, namespaced by tenant and department
    as literal key segments -- isolation is structural, not just a hash
    argument. Only fields that shape the generation go into the hash;
    request_id/agent_id/api_key identify who is asking, not what is being
    asked, and are deliberately excluded so two agents asking the identical
    question hit the same entry. base_url IS included: a model name string is
    only unique within a provider AND an endpoint (an Ollama-hosted model and
    a self-hosted OpenAI-compatible endpoint can share both provider and model
    strings and still answer differently)."""
    payload = {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "messages": [asdict(msg) for msg in messages],
        "tools": [asdict(tool) for tool in tools],
        "params": asdict(params),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"deptcache:{tenant_id}:{department_id}:{digest}"


async def get_cached(key: str) -> CompletionResult | None:
    client = _client()
    try:
        raw = await client.get(key)
    finally:
        await client.aclose()
    if raw is None:
        return None
    data = json.loads(raw)
    return CompletionResult(
        text=data["text"],
        tool_calls=[ToolCall(**tc) for tc in data["tool_calls"]],
        usage=Usage(**data["usage"]),
        stop_reason=data["stop_reason"],
        provider=data["provider"],
        model=data["model"],
    )


async def store(key: str, result: CompletionResult, *, ttl_s: int = _DEFAULT_TTL_S) -> None:
    payload = dataclasses.asdict(result)
    client = _client()
    try:
        await client.set(key, json.dumps(payload), ex=ttl_s)
    finally:
        await client.aclose()


async def delete(key: str) -> None:
    client = _client()
    try:
        await client.delete(key)
    finally:
        await client.aclose()


async def purge_tenant(tenant_id: uuid.UUID) -> None:
    """Delete every department-cache entry for one tenant. Called after an
    operator's irreversible content deletion (see oc8.knowledge.tombstone's
    GDPR "reduce" path): caching has no per-chunk invalidation, so a full-
    tenant purge is the coarse but correct upper bound -- nothing any of this
    tenant's departments could have cached survives past that point."""
    client = _client()
    try:
        cursor = 0
        pattern = f"deptcache:{tenant_id}:*"
        while True:
            cursor, keys = await client.scan(cursor=cursor, match=pattern, count=500)
            if keys:
                await client.delete(*keys)
            if cursor == 0:
                break
    finally:
        await client.aclose()
