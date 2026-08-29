from __future__ import annotations

import logging
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


@pytest.mark.parametrize(
    ("env", "base_url", "warned"),
    [
        # The case this exists for: a real deployment still on the default.
        ("prod", "http://localhost:8080", True),
        # ...and the two that must stay quiet. A laptop is what the default is
        # FOR, and an instance behind a real domain has nothing to warn about.
        ("dev", "http://localhost:8080", False),
        ("prod", "https://oc8.example.test", False),
    ],
)
async def test_a_localhost_link_base_is_warned_about_once_at_startup(
    caplog: pytest.LogCaptureFixture, env: str, base_url: str, warned: bool
) -> None:
    """Every reset/confirmation mail is built off `frontend_base_url`, and
    getting it wrong fails silently: the send succeeds, the endpoint says
    "check your inbox", and the link in that inbox goes nowhere. This log line
    is the only thing that connects that symptom to its cause."""
    from oc8.config import Settings
    from oc8.main import warn_about_unreachable_links

    settings = Settings(env=env, frontend_base_url=base_url)
    with caplog.at_level(logging.WARNING, logger="oc8.main"):
        warn_about_unreachable_links(settings)

    records = [r for r in caplog.records if r.name == "oc8.main"]
    assert bool(records) is warned, [r.getMessage() for r in records]
    if warned:
        assert "OC8_FRONTEND_BASE_URL" in records[0].getMessage()


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
