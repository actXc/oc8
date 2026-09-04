"""`parse_activity` -- a Bot Framework Activity in oc8's vocabulary.

Action.Submit buttons deliver as `type: "message"` Activities carrying a
`value` dict (not `type: "invoke"`, which is Action.Execute's behavior), so
a decision is read off `value`, and everything else falls back to `text`."""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from oc8.channels.notice import ChannelDecision, ChannelFreeText, ChannelLink

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


def _activity(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "type": "message",
        "serviceUrl": "https://smba.trafficmanager.net/teams/",
        "channelId": "msteams",
        "conversation": {"id": "conv-1"},
        "recipient": {"id": "bot-1", "name": "oc8 bot"},
        "from": {"id": "user-1", "name": "Rico"},
    }
    base.update(overrides)
    return base


def test_a_button_submit_parses_as_a_decision() -> None:
    from channel.channel import parse_activity

    approval_id = "00000000-0000-0000-0000-000000000001"
    parsed = parse_activity(_activity(value={"payload": f"{approval_id}:approve:full"}))
    assert isinstance(parsed, ChannelDecision)
    assert str(parsed.approval_id) == approval_id
    assert parsed.verdict == "approve"
    assert parsed.option_key == "full"


def test_a_reject_submit_with_no_option_parses_correctly() -> None:
    from channel.channel import parse_activity

    approval_id = "00000000-0000-0000-0000-000000000002"
    parsed = parse_activity(_activity(value={"payload": f"{approval_id}:reject"}))
    assert isinstance(parsed, ChannelDecision)
    assert parsed.verdict == "reject"
    assert parsed.option_key is None


def test_a_short_bare_text_message_parses_as_a_link_code() -> None:
    from channel.channel import parse_activity

    parsed = parse_activity(_activity(text="abc123XYZ"))
    assert isinstance(parsed, ChannelLink)
    assert parsed.code == "abc123XYZ"


def test_an_ordinary_sentence_parses_as_free_text() -> None:
    from channel.channel import parse_activity

    parsed = parse_activity(_activity(text="Wie viele offene Tickets gibt es?"))
    assert isinstance(parsed, ChannelFreeText)
    assert parsed.text == "Wie viele offene Tickets gibt es?"


def test_an_empty_message_parses_to_nothing() -> None:
    from channel.channel import parse_activity

    assert parse_activity(_activity(text="")) is None


def test_a_non_message_activity_type_parses_to_nothing() -> None:
    """Bot Framework's own housekeeping (typing indicators, conversation
    update events, ...) must never be mistaken for user input."""
    from channel.channel import parse_activity

    assert parse_activity(_activity(type="conversationUpdate", text="")) is None


def test_the_same_conversation_produces_the_same_external_id_every_time() -> None:
    """The conversationReference JSON must be deterministic across separate
    inbound Activities from the SAME user/bot/conversation -- otherwise a
    ChannelLink's external_id (persisted on the binding) would never match a
    later ChannelDecision's external_id for the same person."""
    from channel.channel import parse_activity

    link = parse_activity(_activity(text="abc123XYZ"))
    approval_id = "00000000-0000-0000-0000-000000000003"
    decision = parse_activity(_activity(value={"payload": f"{approval_id}:approve"}))
    assert isinstance(link, ChannelLink)
    assert isinstance(decision, ChannelDecision)
    assert link.external_id == decision.external_id


def test_a_different_conversation_produces_a_different_external_id() -> None:
    from channel.channel import parse_activity

    link_one = parse_activity(_activity(text="abc123XYZ"))
    link_two = parse_activity(_activity(text="abc123XYZ", conversation={"id": "conv-2"}))
    assert isinstance(link_one, ChannelLink)
    assert isinstance(link_two, ChannelLink)
    assert link_one.external_id != link_two.external_id
