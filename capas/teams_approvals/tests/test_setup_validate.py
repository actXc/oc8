"""`channel.setup.validate` -- setup-time proof that a submitted app id and
app password actually work together, mirroring
telegram_approvals/whatsapp_approvals' own setup.py."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _evict() -> None:
    for _stale in [n for n in sys.modules if n == "channel" or n.startswith("channel.")]:
        del sys.modules[_stale]


@pytest.fixture(autouse=True)
def _plugin_path() -> Iterator[None]:
    _evict()
    sys.path.insert(0, str(PLUGIN_ROOT))
    yield
    sys.path.remove(str(PLUGIN_ROOT))
    _evict()


_Handler = Callable[[httpx.Request], httpx.Response]


def _install(monkeypatch: pytest.MonkeyPatch, handler: _Handler) -> None:
    real = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


@pytest.mark.asyncio
async def test_a_missing_app_id_is_rejected_before_any_call() -> None:
    from channel.setup import validate

    with pytest.raises(ValueError, match="app id"):
        await validate({"app_password": "pw"})


@pytest.mark.asyncio
async def test_a_missing_app_password_is_rejected_before_any_call() -> None:
    from channel.setup import validate

    with pytest.raises(ValueError, match="app password"):
        await validate({"app_id": "app-1"})


@pytest.mark.asyncio
async def test_a_working_registration_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.setup import validate

    _install(
        monkeypatch, lambda r: httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
    )
    await validate({"app_id": "app-1", "app_password": "pw"})


@pytest.mark.asyncio
async def test_a_rejected_registration_raises_with_microsofts_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel.setup import validate

    _install(
        monkeypatch,
        lambda r: httpx.Response(
            401, json={"error": "invalid_client", "error_description": "bad secret"}
        ),
    )
    with pytest.raises(ValueError, match="bad secret"):
        await validate({"app_id": "app-1", "app_password": "wrong"})


@pytest.mark.asyncio
async def test_validate_uses_a_configured_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.setup import validate

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})

    _install(monkeypatch, handler)
    await validate({"app_id": "app-1", "app_password": "pw", "tenant_id": "contoso"})

    assert "contoso" in seen["url"]
