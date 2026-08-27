from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from oc8 import models as m
from oc8.constants import ACME_TENANT_ID
from oc8.runtime.queue import RunMessage
from oc8.runtime.repository import RunRepository
from oc8.runtime.states import TERMINAL, RunState
from tests.conftest import AppSessionFactory


@pytest.fixture(autouse=True)
def _reset_otel_providers() -> Iterator[None]:
    # Every existing call site in the app (metering/usage.py, engine.py,
    # executor.py, ingest.py, router.py) now emits through
    # oc8.observability's module-level `_instruments` cache on every test run
    # across the whole suite -- with otel disabled by default, those emits
    # cache no-op instruments under names like "oc8.tokens.in" long before
    # this file's tests ever run. Clear the cache before AND after each test
    # here so a real in-memory provider actually gets a fresh instrument
    # bound to it, not a no-op left over from an unrelated disabled-mode
    # test elsewhere in the suite.
    #
    # Also mirrors the _reset fixture in tests/observability/test_setup.py /
    # test_spans.py: set_meter_provider is "first write wins" (a `Once`
    # guard), so a real SDK provider installed by one test would otherwise
    # silently block later tests in this module from installing their own.
    from oc8.observability import _instruments

    _instruments.clear()
    yield
    _instruments.clear()
    from opentelemetry import trace
    from opentelemetry.util._once import Once

    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    # NOTE: unlike `trace`, `opentelemetry.metrics` does not own its globals
    # directly -- `_METER_PROVIDER`/`_METER_PROVIDER_SET_ONCE` live in the
    # `opentelemetry.metrics._internal` submodule. Resetting
    # `metrics._METER_PROVIDER_SET_ONCE` (mirroring the trace-provider reset
    # pattern in test_setup.py/test_spans.py) silently creates an unrelated
    # attribute on the wrong module and does nothing: set_meter_provider()
    # keeps reading its own module's Once guard, so the second test in this
    # file would fail with "Overriding of current MeterProvider is not
    # allowed" and get_metrics_data() would return None. Reset the submodule
    # that actually backs set_meter_provider/get_meter_provider instead.
    import opentelemetry.metrics._internal as metrics_internal

    metrics_internal._METER_PROVIDER = None
    metrics_internal._METER_PROVIDER_SET_ONCE = Once()


@pytest.fixture
def _in_memory_metrics() -> Iterator[Any]:
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    try:
        yield reader
    finally:
        provider.shutdown()


def test_token_counters_record(_in_memory_metrics: Any) -> None:
    from oc8.observability import record_token_usage

    record_token_usage(provider="ollama", model="m", tokens_in=10, tokens_out=5)
    data = _in_memory_metrics.get_metrics_data()
    # Assert at least one metric named oc8.tokens.in appears with value 10.
    names = _metric_points(data)
    assert names.get("oc8.tokens.in") == 10
    assert names.get("oc8.tokens.out") == 5


def test_run_outcome_counter_records(_in_memory_metrics: Any) -> None:
    from oc8.observability import record_run_outcome

    record_run_outcome("done")
    data = _in_memory_metrics.get_metrics_data()
    assert _metric_points(data).get("oc8.runs.total") == 1


def test_run_outcome_labels_used_by_previously_missing_terminal_paths_are_valid() -> None:
    # The five terminal transitions that used to fall through without emitting
    # record_run_outcome (agent-not-found, hire-gate block, cancel-while-queued,
    # unknown-MCP, recover_reclaimed) pass either RunState.FAILED.value or
    # RunState.INTERRUPTED.value -- the same low-cardinality label form the two
    # pre-existing call sites in executor.py use (RunState.<x>.value). Guard
    # that those two strings stay valid, TERMINAL RunState values so the label
    # never silently drifts from the enum.
    for outcome in ("failed", "interrupted"):
        state = RunState(outcome)
        assert state.value == outcome
        assert state in TERMINAL


async def test_execute_run_records_outcome_for_cancel_while_queued(
    _in_memory_metrics: Any, app_session: AppSessionFactory
) -> None:
    """Drives the cancel-while-queued terminal path (previously silent in the
    oc8.runs.total counter -- see executor.py) end-to-end through execute_run
    and asserts the counter now records it with outcome="interrupted"."""
    from oc8.runtime.executor import execute_run

    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as s:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="Dev")
        s.add(agent)
        await s.flush()
        repo = RunRepository(s)
        run = await repo.create(tenant_id=tenant, agent_id=agent.id, context={"task": "do it"})
        run_id = run.id
        s.add(
            m.RunCancellation(
                tenant_id=tenant,
                run_id=run_id,
                requested_at=dt.datetime.now(tz=dt.UTC),
                cancellation_kind="operator_interrupted",
            )
        )

    async def must_not_run(db: Any, **kw: Any) -> Any:
        raise AssertionError("a cancelled queued run must not invoke the runtime")

    class _FnRuntime:
        async def execute(self, db: Any, **kw: Any) -> Any:
            return await must_not_run(db, **kw)

    await execute_run(
        RunMessage(run_id=str(run_id), tenant_id=str(tenant), entry_id="0-0", redelivered=False),
        runtime=_FnRuntime(),
    )

    async with app_session(tenant) as s:
        refreshed = await s.get(m.AgentRun, run_id)
        assert refreshed is not None
        assert refreshed.state == RunState.INTERRUPTED.value

    data = _in_memory_metrics.get_metrics_data()
    assert _metric_points(data).get("oc8.runs.total") == 1


def _metric_points(data: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                total = 0.0
                for pt in metric.data.data_points:
                    total += getattr(pt, "value", 0)
                out[metric.name] = total
    return out
