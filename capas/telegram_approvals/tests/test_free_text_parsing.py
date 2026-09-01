"""`TelegramChannel.parse_inbound` -- the free-text branch (Task 7 of the
oc8 Assistant/Telegram plan). Confirms an ordinary message resolves to a
`ChannelFreeText`, while a `/start <code>` link attempt and a callback
button tap still resolve to their existing variants, not this new one."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from oc8.channels.notice import ChannelDecision, ChannelFreeText, ChannelLink

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _evict() -> None:
    """Drop any cached `channel` module before/after each test -- `channel/`
    is a shared folder name across plugins (this one AND whatsapp_approvals
    both ship one), and sys.modules is keyed by NAME, not path. Mirrors
    google_workspace/tests/test_connector.py's convention for the same
    generic-name collision."""
    for _stale in [n for n in sys.modules if n == "channel" or n.startswith("channel.")]:
        del sys.modules[_stale]


@pytest.fixture(autouse=True)
def _plugin_path() -> Iterator[None]:
    """Make THIS plugin's `channel` package the one that resolves, for each
    test. The real runtime reaches it through `oc8.capas.loader`, which
    inserts the plugin folder into `sys.path` itself; a test that never
    calls the loader needs the same insertion done by hand."""
    _evict()
    sys.path.insert(0, str(PLUGIN_ROOT))
    yield
    sys.path.remove(str(PLUGIN_ROOT))
    _evict()


def _channel():
    from channel.channel import TelegramChannel

    return TelegramChannel(token="t", max_classification="public", webhook_secret="s")


def test_a_bare_text_message_parses_as_free_text() -> None:
    update = {"message": {"text": "Bitte alle Tickets abarbeiten", "from": {"id": 42}}}
    parsed = _channel().parse_inbound(update)
    assert isinstance(parsed, ChannelFreeText)
    assert parsed.text == "Bitte alle Tickets abarbeiten"
    assert parsed.external_id == "42"


def test_a_start_command_with_a_bare_code_is_still_a_link_not_free_text() -> None:
    update = {"message": {"text": "/start abc123XYZ", "from": {"id": 42}}}
    parsed = _channel().parse_inbound(update)
    assert isinstance(parsed, ChannelLink)


def test_a_callback_button_tap_is_still_a_decision_not_free_text() -> None:
    update = {
        "callback_query": {
            "id": "cb1",
            "data": "00000000-0000-0000-0000-000000000001:approve",
            "from": {"id": 42},
        }
    }
    parsed = _channel().parse_inbound(update)
    assert isinstance(parsed, ChannelDecision)


def test_an_empty_message_parses_to_nothing() -> None:
    update = {"message": {"text": "", "from": {"id": 42}}}
    assert _channel().parse_inbound(update) is None


def test_a_message_with_no_sender_parses_to_nothing() -> None:
    update = {"message": {"text": "hello"}}
    assert _channel().parse_inbound(update) is None
