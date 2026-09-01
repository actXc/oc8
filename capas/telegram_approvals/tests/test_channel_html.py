"""`_to_telegram_html` -- converts the Assistant's Markdown-flavoured free-text
replies into Telegram's HTML parse mode, so `**bold**`/`- ` bullets render
instead of showing up as literal asterisks and dashes on the reader's phone."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _evict() -> None:
    """Drop any cached `channel` module before/after each test -- `channel/`
    is a shared folder name across plugins (this one AND whatsapp_approvals
    both ship one), and sys.modules is keyed by NAME, not path. Mirrors
    test_free_text_parsing.py's convention for the same generic-name
    collision."""
    for _stale in [n for n in sys.modules if n == "channel" or n.startswith("channel.")]:
        del sys.modules[_stale]


@pytest.fixture(autouse=True)
def _plugin_path() -> Iterator[None]:
    _evict()
    sys.path.insert(0, str(PLUGIN_ROOT))
    yield
    sys.path.remove(str(PLUGIN_ROOT))
    _evict()


def test_double_asterisk_bold_becomes_a_real_tag() -> None:
    from channel.channel import _to_telegram_html

    assert _to_telegram_html("**26 offene Tickets**") == "<b>26 offene Tickets</b>"


def test_a_leading_dash_bullet_becomes_a_bullet_point() -> None:
    from channel.channel import _to_telegram_html

    assert _to_telegram_html("- Customer Care (9)") == "• Customer Care (9)"


def test_bold_and_bullets_combine_on_one_line() -> None:
    from channel.channel import _to_telegram_html

    result = _to_telegram_html("- **26 offene Tickets** insgesamt")
    assert result == "• <b>26 offene Tickets</b> insgesamt"


def test_html_special_characters_in_the_models_own_text_are_escaped() -> None:
    from channel.channel import _to_telegram_html

    result = _to_telegram_html("Odoo <Ticket> Preis & Menge > 5")
    assert result == "Odoo &lt;Ticket&gt; Preis &amp; Menge &gt; 5"
    assert "<Ticket>" not in result


def test_plain_text_with_no_markdown_passes_through_unchanged() -> None:
    from channel.channel import _to_telegram_html

    text = "Lennart kümmert sich darum und meldet sich in Kürze."
    assert _to_telegram_html(text) == text


def test_a_stray_single_asterisk_is_left_alone_not_treated_as_markup() -> None:
    """Single-asterisk italics/emphasis are deliberately NOT converted -- too
    easy to false-positive on snake_case, multiplication, or a lone star."""
    from channel.channel import _to_telegram_html

    assert _to_telegram_html("5 * 3 = 15") == "5 * 3 = 15"


def test_a_mid_word_dash_is_not_mistaken_for_a_bullet() -> None:
    """Only a dash at the START of a line is a list marker -- 'Odoo-Tickets'
    must not lose its hyphen."""
    from channel.channel import _to_telegram_html

    assert _to_telegram_html("Odoo-Tickets sind offen") == "Odoo-Tickets sind offen"


def test_multiline_reply_converts_each_line_independently() -> None:
    from channel.channel import _to_telegram_html

    text = "**Zusammenfassung:**\n- 26 offene Tickets\n- Customer Care (9)"
    expected = "<b>Zusammenfassung:</b>\n• 26 offene Tickets\n• Customer Care (9)"
    assert _to_telegram_html(text) == expected


@pytest.mark.asyncio
async def test_say_sends_html_parse_mode_with_converted_text() -> None:
    from channel.channel import TelegramChannel

    ch = TelegramChannel(token="t")
    calls: list[tuple[str, dict[str, object]]] = []

    async def _fake_post(method: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((method, payload))
        return {}

    ch._post = _fake_post  # type: ignore[method-assign]
    await ch.say("123", "**Danke** für das Ergebnis!")

    assert len(calls) == 1
    method, payload = calls[0]
    assert method == "sendMessage"
    assert payload["chat_id"] == "123"
    assert payload["parse_mode"] == "HTML"
    assert payload["text"] == "<b>Danke</b> für das Ergebnis!"


@pytest.mark.asyncio
async def test_say_still_swallows_a_send_failure() -> None:
    """Pre-existing best-effort contract: a Telegram send failure must never
    raise out of say() -- unchanged by the HTML conversion."""
    from channel.channel import TelegramChannel

    ch = TelegramChannel(token="t")

    async def _boom(method: str, payload: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("network down")

    ch._post = _boom  # type: ignore[method-assign]
    await ch.say("123", "hello")
