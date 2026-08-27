"""nanoclaw's row shapes -> neutral oc8 agent messages.

This is where the vendor knowledge is allowed to live. The core only ever sees
`AgentMessage`, the same rule that keeps Odoo out of the runtime.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# capas/nanoclaw_runtime/tests/test_messages.py -> parents[1] is the plugin root,
# which is the directory `runtime/` is imported from. This plugin is NOT on pytest's
# `pythonpath` any more (see backend/pyproject.toml): every plugin resolves its own
# package from its own test module, because all four runtime_adapter plugins ship a
# package called `runtime` and a blanket path list would silently hand
# `import runtime` to whichever root sorts first.
# Module-top form, because this file's plugin imports are module-level and therefore
# resolve at COLLECTION time -- a function-scoped fixture would run far too late.
PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _evict() -> None:
    """Drop every cached `runtime`/`runtime.*` module from sys.modules.

    `runtime` is the package name EVERY `runtime_adapter` plugin now ships --
    nanoclaw_runtime, claude_code_runtime, codex_runtime and opencode_runtime,
    four plugins behind one top-level module name (design §2) -- and sys.modules
    is keyed by NAME, not by path. Called SYMMETRICALLY, before this module's
    import AND after it: before, so a sibling runtime's cached copy cannot answer
    ours; after, so nothing generic is left cached for anyone else. The trailing
    half is the load-bearing one -- `loader.import_entry_point` (Task 6's
    collision fix) only evicts modules IT ITSELF introduced, so a `runtime` left
    cached here makes a later `find_plugin`/`load_plugin` for one of the other
    three silently hand back THIS plugin's `register`.
    """
    for _stale in [n for n in sys.modules if n == "runtime" or n.startswith("runtime.")]:
        del sys.modules[_stale]


_evict()
sys.path.insert(0, str(PLUGIN_ROOT))

from runtime.messages import to_agent_message  # noqa: E402
from runtime.session_db import OutRow  # noqa: E402

# Symmetric with the insert above: the names this module-level import needed are
# already bound into this module's own namespace, so nothing here re-resolves
# `runtime` -- but leaving it cached would hand THIS plugin's module to whichever
# sibling runtime's test file collects next.
_evict()
sys.path.remove(str(PLUGIN_ROOT))


def _row(kind: str, content: dict[str, object]) -> OutRow:
    return OutRow(seq=1, kind=kind, content=json.dumps(content), timestamp="t")


def test_a_plain_chat_reply_becomes_an_info_message() -> None:
    msg = to_agent_message(_row("chat", {"text": "Angebot erstellt"}))

    assert msg is not None
    assert msg.text == "Angebot erstellt"
    assert msg.status == "info"


def test_a_chat_sdk_reply_uses_its_markdown() -> None:
    msg = to_agent_message(_row("chat-sdk", {"markdown": "## Fertig\nAlles erledigt"}))

    assert msg is not None
    assert "Alles erledigt" in msg.text


def test_a_system_row_is_dropped_rather_than_shown_to_an_operator() -> None:
    assert to_agent_message(_row("system", {"operation": "self_mod"})) is None


def test_an_edit_or_reaction_is_dropped_by_its_nested_operation() -> None:
    """kind is still 'chat' for these -- the discriminant is inside content, so a
    kind-only filter would surface an emoji as if it were the agent's answer."""
    assert to_agent_message(_row("chat", {"operation": "reaction", "emoji": "thumbsup"})) is None
    assert to_agent_message(_row("chat", {"operation": "edit", "text": "korrigiert"})) is None


def test_a_question_card_is_surfaced_with_its_question_text_and_options() -> None:
    """A chat-sdk row carrying an ask_user_question: the operator should see what
    the agent is asking, not silence. The real shape from interactive.ts includes
    options array with label, selectedLabel, value fields."""
    msg = to_agent_message(
        _row(
            "chat-sdk",
            {
                "type": "ask_question",
                "question": "Welchen Kunden meinst du?",
                "options": [
                    {"label": "Acme Corp", "selectedLabel": "Acme Corp", "value": "acme"},
                    {"label": "Tech Inc", "selectedLabel": "Tech Inc", "value": "techinc"},
                ],
            },
        )
    )

    assert msg is not None
    assert "Welchen Kunden" in msg.text
    assert "Acme Corp" in msg.text
    assert "Tech Inc" in msg.text


def test_a_card_row_uses_fallback_text_if_present() -> None:
    """send_card rows have shape {card, fallbackText}. The rendering precedence
    mirrors chat-sdk-bridge.ts line 469: fallbackText > card.description > card.title.
    This is the first choice."""
    msg = to_agent_message(
        _row(
            "chat-sdk",
            {
                "type": "card",
                "fallbackText": "Important note for offline",
                "card": {"title": "Card Title", "description": "Card description"},
            },
        )
    )

    assert msg is not None
    assert msg.text == "Important note for offline"


def test_a_card_row_uses_card_description_if_no_fallback() -> None:
    """When fallbackText is absent, try card.description next."""
    msg = to_agent_message(
        _row(
            "chat-sdk",
            {
                "type": "card",
                "card": {"title": "Card Title", "description": "A detailed description"},
            },
        )
    )

    assert msg is not None
    assert msg.text == "A detailed description"


def test_a_card_row_falls_back_to_title() -> None:
    """When fallbackText and description are absent, use card.title."""
    msg = to_agent_message(
        _row(
            "chat-sdk",
            {"type": "card", "card": {"title": "Only the title"}},
        )
    )

    assert msg is not None
    assert msg.text == "Only the title"


def test_a_card_row_with_empty_card_is_dropped() -> None:
    """A card row with no usable text is silently dropped, not crashed."""
    assert to_agent_message(_row("chat-sdk", {"type": "card", "card": {}})) is None


def test_an_unparseable_row_does_not_kill_the_run() -> None:
    assert to_agent_message(OutRow(seq=1, kind="chat", content="not json", timestamp="t")) is None


def test_an_empty_text_is_dropped() -> None:
    assert to_agent_message(_row("chat", {"text": "   "})) is None
