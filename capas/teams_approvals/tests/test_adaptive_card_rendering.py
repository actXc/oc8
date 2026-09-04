"""`render_card`/`render_outcome_card` -- an ApprovalNotice as an Adaptive
Card with Action.Submit buttons carrying `{approval_id}:{verdict}[:{option}]`
in their `data`, the same encoding Telegram's/WhatsApp's own callback/reply
ids use."""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from oc8.channels.notice import ApprovalNotice, NoticeOption

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


_APPROVAL_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


def _notice(**overrides: object) -> ApprovalNotice:
    defaults: dict[str, object] = {
        "approval_id": _APPROVAL_ID,
        "tenant_id": _TENANT_ID,
        "title": "Rechnung freigeben?",
        "detail": "Rechnung #123 über 450 EUR",
        "amount_text": "450,00 EUR",
    }
    defaults.update(overrides)
    return ApprovalNotice(**defaults)  # type: ignore[arg-type]


def test_the_card_body_carries_title_amount_and_detail() -> None:
    from channel.channel import render_body

    body = render_body(_notice())
    texts = [b["text"] for b in body if b["type"] == "TextBlock"]
    assert "Rechnung freigeben?" in texts
    assert "450,00 EUR" in texts
    assert "Rechnung #123 über 450 EUR" in texts


def test_a_withheld_notice_gets_no_detail_and_a_pointer_to_oc8() -> None:
    from channel.channel import render_body

    body = render_body(_notice().redacted())
    texts = [b["text"] for b in body if b["type"] == "TextBlock"]
    assert "Rechnung #123 über 450 EUR" not in texts
    assert any("oc8" in t for t in texts)


def test_options_render_as_approve_actions_with_the_option_key_in_the_payload() -> None:
    from channel.channel import render_actions

    notice = _notice(options=(NoticeOption(key="full", label="Voll erstatten"),))
    actions = render_actions(notice)
    payloads = [a["data"]["payload"] for a in actions]
    assert f"{_APPROVAL_ID}:approve:full" in payloads
    assert f"{_APPROVAL_ID}:reject" in payloads


def test_no_options_still_gets_a_plain_approve_and_reject_pair() -> None:
    from channel.channel import render_actions

    actions = render_actions(_notice())
    payloads = [a["data"]["payload"] for a in actions]
    assert f"{_APPROVAL_ID}:approve" in payloads
    assert f"{_APPROVAL_ID}:reject" in payloads


def test_a_withheld_notice_gets_no_actions_at_all() -> None:
    """A button labelled "Voll erstatten" tells you what the request is
    about even with the text removed -- exactly what withholding it was
    for."""
    from channel.channel import render_actions

    assert render_actions(_notice().redacted()) == []


def test_render_card_is_a_well_formed_adaptive_card_with_actions() -> None:
    from channel.channel import render_card

    card = render_card(_notice())
    assert card["type"] == "AdaptiveCard"
    assert card["body"]
    assert card["actions"]


def test_render_card_omits_the_actions_key_entirely_when_withheld() -> None:
    from channel.channel import render_card

    card = render_card(_notice().redacted())
    assert "actions" not in card


def test_render_outcome_card_states_the_decision_and_has_no_actions() -> None:
    from channel.channel import render_outcome_card

    card = render_outcome_card(_notice(), outcome="approved")
    texts = [b["text"] for b in card["body"] if b["type"] == "TextBlock"]
    assert any("freigegeben" in t for t in texts)
    assert "actions" not in card
