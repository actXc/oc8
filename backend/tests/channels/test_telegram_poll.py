"""TelegramChannel.poll() -- the offset bookkeeping getUpdates needs, tested
against a mocked HTTP layer (no real Telegram connection). See
test_telegram.py's module docstring for why the plugin's `channel` package
must be evicted from sys.modules before this import: it shares a bare
`channel` package name with whatsapp_approvals's own retrofitted layout."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio

PLUGIN_ROOT = Path(__file__).resolve().parents[3] / "capas" / "telegram_approvals"


def _evict_generic_plugin_packages() -> None:
    for _stale in [n for n in sys.modules if n == "channel" or n.startswith("channel.")]:
        del sys.modules[_stale]


_evict_generic_plugin_packages()
sys.path.insert(0, str(PLUGIN_ROOT))

from channel.channel import TelegramChannel  # noqa: E402

_evict_generic_plugin_packages()
sys.path.remove(str(PLUGIN_ROOT))


def _mock_response(payload: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


async def test_a_first_poll_advances_past_the_highest_update_id() -> None:
    channel = TelegramChannel(token="t")
    body = {
        "ok": True,
        "result": [
            {"update_id": 100, "message": {"text": "hi"}},
            {"update_id": 102, "message": {"text": "hi"}},
            {"update_id": 101, "message": {"text": "hi"}},
        ],
    }
    with patch("httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.post.return_value = _mock_response(body)
        client_cls.return_value.__aenter__.return_value = client
        updates, next_offset = await channel.poll(offset=0)

    assert len(updates) == 3
    assert next_offset == 103, "one past the HIGHEST update_id, not the last one in the list"
    sent = client.post.call_args
    assert sent.kwargs["json"] == {"offset": 0, "timeout": 0}, "must not block the shared tick"


async def test_no_updates_leaves_the_offset_unchanged() -> None:
    channel = TelegramChannel(token="t")
    with patch("httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.post.return_value = _mock_response({"ok": True, "result": []})
        client_cls.return_value.__aenter__.return_value = client
        updates, next_offset = await channel.poll(offset=42)

    assert updates == []
    assert next_offset == 42


async def test_the_next_call_passes_the_previous_offset_back() -> None:
    """Telegram's own contract: sending its last-seen `update_id + 1` back as
    `offset` is what marks those updates delivered and read."""
    channel = TelegramChannel(token="t")
    with patch("httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.post.return_value = _mock_response({"ok": True, "result": []})
        client_cls.return_value.__aenter__.return_value = client
        await channel.poll(offset=250)

    sent = client.post.call_args
    assert sent.kwargs["json"]["offset"] == 250
