from __future__ import annotations

from collections.abc import Iterator

import pytest
from asgi_lifespan import LifespanManager

from oc8.main import create_app

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_otel_providers() -> Iterator[None]:
    # test_app_starts_with_otel_console below installs a real SDK TracerProvider
    # via the app lifespan. shutdown_observability() only shuts down processors,
    # it doesn't (and in production shouldn't) unset the global provider -- OTel's
    # set_tracer_provider/set_meter_provider are "first write wins". Without this,
    # the provider bleeds into other test modules (e.g. tests/observability/test_setup.py)
    # collected afterwards. Mirrors the _reset fixture in test_setup.py.
    yield
    from opentelemetry import metrics, trace
    from opentelemetry.util._once import Once

    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    metrics._METER_PROVIDER = None  # type: ignore[attr-defined]
    metrics._METER_PROVIDER_SET_ONCE = Once()  # type: ignore[attr-defined]


async def test_app_starts_with_otel_disabled() -> None:
    # The default (disabled) path must start and stop cleanly — the regression bar.
    app = create_app()
    async with LifespanManager(app):
        pass  # startup + shutdown run without error


async def test_app_starts_with_otel_console(monkeypatch: pytest.MonkeyPatch) -> None:
    from oc8 import config

    s = config.get_settings()
    monkeypatch.setattr(s, "otel_enabled", True, raising=False)
    monkeypatch.setattr(s, "otel_exporter", "console", raising=False)
    monkeypatch.setattr(s, "otel_metrics_enabled", False, raising=False)
    app = create_app(s)
    async with LifespanManager(app):
        pass  # enabling observability must not break startup
    from oc8.observability import shutdown_observability

    shutdown_observability()
