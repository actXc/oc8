"""WhatsApp rendering and inbound parsing.

The second channel exists to show the seam holds: core did not change to gain
it. What is worth testing is what WhatsApp does differently and silently — three
buttons of twenty characters, a signature over the raw body, and a 24-hour
window that makes "unsolicited" a lie this channel must not tell.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import uuid
from pathlib import Path

import pytest

from oc8.channels.notice import ApprovalNotice, ChannelDecision, ChannelLink, NoticeOption

#: whatsapp_approvals's channel package is named `channel` after the
#: restructure -- a name shared with telegram_approvals and every other
#: retrofitted plugin -- so sys.modules (keyed by NAME, not path) can hand
#: this import a sibling's cached copy. Both this file and test_telegram.py
#: import at COLLECTION time (module top, not inside a test function), so a
#: function-scoped autouse fixture cannot help; the eviction has to run in
#: the module body itself, unconditionally, before its own import, since
#: pytest's collection order between the two files is not guaranteed.
PLUGIN_ROOT = Path(__file__).resolve().parents[3] / "capas" / "whatsapp_approvals"


def _evict_generic_plugin_packages() -> None:
    for _stale in [n for n in sys.modules if n == "channel" or n.startswith("channel.")]:
        del sys.modules[_stale]


_evict_generic_plugin_packages()
sys.path.insert(0, str(PLUGIN_ROOT))

from channel.channel import (  # noqa: E402
    build,
    buttons,
    parse_reply,
    render,
    verify_signature,
)

# Symmetric with the insert above: the names this import needed are already
# bound into this module's own namespace, so nothing later re-resolves
# `channel` for this file -- but leaving it cached would let whichever of
# this file / test_telegram.py collects SECOND silently receive THIS
# plugin's module for its own `channel.channel` import.
_evict_generic_plugin_packages()
sys.path.remove(str(PLUGIN_ROOT))


def _notice(**kw: object) -> ApprovalNotice:
    base: dict[str, object] = {
        "approval_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "title": "Erstattung freigeben?",
        "detail": "Ticket #42, 249 EUR doppelt abgebucht.",
        "amount_text": "249,00 EUR",
    }
    base.update(kw)
    return ApprovalNotice(**base)  # type: ignore[arg-type]


def _message(**content: object) -> dict[str, object]:
    return {"entry": [{"changes": [{"value": {"messages": [{"from": "4915100", **content}]}}]}]}


def test_this_channel_admits_it_cannot_always_speak_first() -> None:
    """WhatsApp allows free-form messages only within 24 hours of the person
    writing. Declaring `unsolicited=True` here would be a promise the platform
    does not let this channel keep."""
    channel = build({"bot_token": "t", "phone_number_id": "1"})
    assert channel.capabilities().unsolicited is False


def test_at_most_three_buttons_and_refusing_always_survives() -> None:
    """Meta caps interactive replies at three; extra ones do not appear at all.
    Dropping options is a loss, dropping "Ablehnen" would mean saying no
    requires leaving the chat."""
    notice = _notice(
        options=tuple(NoticeOption(f"k{i}", f"Option {i}") for i in range(5))
    )
    rows = buttons(notice)
    assert len(rows) <= 3
    titles = [r["reply"]["title"] for r in rows]
    assert "Ablehnen" in titles
    assert all(len(t) <= 20 for t in titles), "a longer title rejects the whole message"


def test_a_withheld_notice_gets_no_buttons_and_no_content() -> None:
    notice = _notice(options=(NoticeOption("full", "Voll erstatten"),)).redacted()
    assert buttons(notice) == []
    body = render(notice)
    assert "249" not in body and "oc8" in body


def test_a_button_reply_becomes_a_decision() -> None:
    approval = uuid.uuid4()
    parsed = parse_reply(
        _message(
            interactive={
                "type": "button_reply",
                "button_reply": {"id": f"{approval}:approve:full", "title": "Voll erstatten"},
            }
        )
    )
    assert isinstance(parsed, ChannelDecision)
    assert parsed.approval_id == approval
    assert parsed.verdict == "approve"
    assert parsed.option_key == "full"
    assert parsed.external_id == "4915100"


def test_a_single_word_message_is_read_as_a_binding_code() -> None:
    parsed = parse_reply(_message(text={"body": "abc123"}))
    assert isinstance(parsed, ChannelLink)
    assert parsed.code == "abc123" and parsed.external_id == "4915100"


def test_a_delivery_receipt_is_not_an_event_we_care_about() -> None:
    """Meta sends status updates through the same webhook. Returning None has to
    be a normal outcome, not an error."""
    receipt = {"entry": [{"changes": [{"value": {"statuses": [{"status": "read"}]}}]}]}
    assert parse_reply(receipt) is None
    assert parse_reply({}) is None
    assert parse_reply(_message(text={"body": "hallo wie geht es"})) is None


def test_the_signature_is_checked_against_the_bytes_meta_sent() -> None:
    """Over the raw body, never a re-serialised parse: JSON that round-trips
    with different spacing is a signature that fails for everyone."""
    secret = "app-secret"
    body = json.dumps({"entry": []}, separators=(",", ":")).encode()
    good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert verify_signature(headers={"x-hub-signature-256": good}, body=body, app_secret=secret)
    assert not verify_signature(
        headers={"x-hub-signature-256": good}, body=body + b" ", app_secret=secret
    )
    assert not verify_signature(headers={}, body=body, app_secret=secret)


def test_an_unconfigured_secret_refuses_every_call() -> None:
    """Fails closed. The webhook URL is not a secret -- it sits in Meta's logs,
    in a proxy's, and in whatever the operator pasted it into -- so an
    unverified call reaching the decision path would let anyone approve
    anything."""
    assert not verify_signature(
        headers={"x-hub-signature-256": "sha256=whatever"}, body=b"{}", app_secret=""
    )


def test_a_channel_missing_either_credential_refuses_to_exist() -> None:
    with pytest.raises(ValueError, match="access token"):
        build({})
    with pytest.raises(ValueError, match="phone_number_id"):
        build({"bot_token": "t"})
