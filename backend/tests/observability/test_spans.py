from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.engine import run_agent
from oc8.modelrouter import CompletionResult, Usage, chunk_from_result
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_otel_providers() -> Iterator[None]:
    # set_tracer_provider is "first write wins" (a `Once` guard): once one test
    # in this module installs a real SDK provider, a later test's call is
    # silently ignored unless the private globals are reset. Mirrors the
    # _reset fixture in tests/observability/test_setup.py.
    yield
    from opentelemetry import metrics, trace
    from opentelemetry.util._once import Once

    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    metrics._METER_PROVIDER = None  # type: ignore[attr-defined]
    metrics._METER_PROVIDER_SET_ONCE = Once()  # type: ignore[attr-defined]


@pytest.fixture
def _in_memory_spans() -> Iterator[Any]:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    try:
        yield exporter
    finally:
        provider.shutdown()
        # restore is best-effort; the API forbids re-setting, so tests stay isolated
        # by asserting only on this exporter's spans.


class _OneCall:
    def __init__(self) -> None:
        self.n = 0

    async def complete(self, req: Any) -> CompletionResult:
        self.n += 1
        return CompletionResult(
            text="done", tool_calls=[], usage=Usage(1, 1),
            stop_reason="stop", provider="ollama", model="m",
        )

    async def stream(self, req: Any) -> Any:
        yield chunk_from_result(await self.complete(req))


async def test_agent_run_emits_spans(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch, _in_memory_spans: Any
) -> None:
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: _OneCall())
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Ops", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Rep")
        db.add(agent)
        await db.flush()
        await run_agent(db, agent=agent, task_text="do it", tenant_id=tenant)

    names = {s.name for s in _in_memory_spans.get_finished_spans()}
    assert "agent.run" in names
    # Stage 2: the in-process engine calls stream_completion_with_fallback
    # now, not complete_with_fallback -- oc8.modelrouter.fallback's own
    # tracer span is named for whichever one actually ran.
    assert "model.stream" in names
