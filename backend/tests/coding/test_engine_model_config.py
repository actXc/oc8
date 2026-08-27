# backend/tests/coding/test_engine_model_config.py
from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from oc8 import models as m
from oc8.agent.engine import run_agent
from oc8.constants import ACME_TENANT_ID
from oc8.modelrouter import CompletionResult, Usage, chunk_from_result
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


class _CapturingCompletionRouter:
    def __init__(self) -> None:
        self.captured_requests: list[Any] = []

    async def complete(self, req: Any) -> CompletionResult:
        self.captured_requests.append(req)
        return CompletionResult(
            text="done", tool_calls=[], usage=Usage(tokens_in=3, tokens_out=3),
            stop_reason="stop", provider=req.provider, model=req.model,
        )

    async def stream(self, req: Any) -> Any:
        yield chunk_from_result(await self.complete(req))


async def _make_dept(db: Any, tenant: uuid.UUID) -> m.Department:
    dept = m.Department(tenant_id=tenant, name="Ops", frame={})
    db.add(dept)
    await db.flush()
    return dept


async def test_agent_with_model_config_uses_its_provider_and_locality(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        dept = await _make_dept(db, tenant)
        model_config = m.ModelConfig(
            tenant_id=tenant, provider="openai", model="gpt-4o-mini", locality="cloud"
        )
        db.add(model_config)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant, department_id=dept.id, name="Counsel",
            model_config_id=model_config.id,
        )
        db.add(agent)
        await db.flush()

        completion_router = _CapturingCompletionRouter()
        monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: completion_router)

        result = await run_agent(db, agent=agent, task_text="do work", tenant_id=tenant)
        assert result.status == "done"
        assert completion_router.captured_requests[0].provider == "openai"
        assert completion_router.captured_requests[0].model == "gpt-4o-mini"


async def test_agent_without_model_config_uses_presentation_fallback(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        dept = await _make_dept(db, tenant)
        agent = m.Agent(
            tenant_id=tenant, department_id=dept.id, name="Counsel",
            presentation={"provider": "anthropic"},
        )
        db.add(agent)
        await db.flush()
        # No model_config_id set.

        completion_router = _CapturingCompletionRouter()
        monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: completion_router)

        result = await run_agent(db, agent=agent, task_text="do work", tenant_id=tenant)
        assert result.status == "done"
        assert completion_router.captured_requests[0].provider == "anthropic"


async def test_run_agent_falls_back_through_model_config_chain(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        dept = await _make_dept(db, tenant)
        fallback_config = m.ModelConfig(
            tenant_id=tenant, provider="ollama", model="mistral:latest", locality="local"
        )
        db.add(fallback_config)
        await db.flush()
        primary_config = m.ModelConfig(
            tenant_id=tenant, provider="openai", model="gpt-4o", locality="cloud",
            fallbacks=[str(fallback_config.id)],
        )
        db.add(primary_config)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant, department_id=dept.id, name="Rep",
            model_config_id=primary_config.id,
        )
        db.add(agent)
        await db.flush()

        class _FailThenSucceedRouter:
            def __init__(self) -> None:
                self.calls = 0

            async def complete(self, req: Any) -> CompletionResult:
                self.calls += 1
                if req.provider == "openai":
                    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
                    response = httpx.Response(503, request=request)
                    raise httpx.HTTPStatusError("server error", request=request, response=response)
                return CompletionResult(
                    text="done", tool_calls=[], usage=Usage(tokens_in=3, tokens_out=3),
                    stop_reason="stop", provider=req.provider, model=req.model,
                )

            async def stream(self, req: Any) -> Any:
                yield chunk_from_result(await self.complete(req))

        fake_router = _FailThenSucceedRouter()
        monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: fake_router)

        result = await run_agent(db, agent=agent, task_text="do work", tenant_id=tenant)
        assert result.status == "done"
        assert fake_router.calls == 2
