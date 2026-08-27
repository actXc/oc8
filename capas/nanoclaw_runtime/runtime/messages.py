"""Translate nanoclaw's messages_out rows into neutral oc8 agent messages.

All knowledge of nanoclaw's row shapes stops here. The adapter hands
`AgentMessage`s to oc8's ordinary message flow, so the live transcript in the
frontend shows a nanoclaw run exactly like an in-process one.

The shape has two axes, which the design doc originally conflated (corrected by
Task 0's spike): `kind` is only ever `chat`, `chat-sdk` or `system`, while
`card`/`question` live in a chat-sdk row's `content.type` and `edit`/`reaction` in
a chat row's `content.operation`. So: switch on `kind`, then on the nested
discriminant. A kind-only filter would show an emoji reaction as if it were the
agent's answer.

A row we cannot read is DROPPED, never raised: a malformed line from the harness
must not end a run that is otherwise working.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .session_db import OutRow

#: Outer kinds that are never operator-facing. `task_log` is unreachable here --
#: it needs inbound rows of kind='task', which oc8 never writes -- but naming it
#: costs nothing and documents why it is absent.
_SILENT_KINDS = frozenset({"system", "task_log"})
#: Nested operations inside a `chat` row that edit or decorate an earlier
#: message rather than saying something new.
_SILENT_OPERATIONS = frozenset({"edit", "reaction", "typing", "delete"})


@dataclass(frozen=True)
class AgentMessage:
    text: str
    status: str = "info"


def to_agent_message(row: OutRow) -> AgentMessage | None:
    if row.kind in _SILENT_KINDS:
        return None
    try:
        content = json.loads(row.content)
    except (ValueError, TypeError):
        return None
    if not isinstance(content, dict):
        return None
    if str(content.get("operation") or "") in _SILENT_OPERATIONS:
        return None
    text = _text_of(content)
    return AgentMessage(text=text) if text else None


def _text_of(content: dict[str, Any]) -> str:
    """The operator-facing text of a row, whatever shape it arrived in."""
    # Check for plain text or question
    value = ""
    for key in ("text", "question"):
        value = str(content.get(key) or "").strip()
        if value:
            break

    # markdown is defensive: no write path in nanoclaw's pinned commit produces it,
    # so it exists to survive a future upstream shape rather than serve a known one.
    if not value:
        value = str(content.get("markdown") or "").strip()

    # If we have a question, append options if present
    if content.get("question") and "options" in content:
        options = content.get("options")
        if isinstance(options, list) and options:
            option_labels = []
            for opt in options:
                if isinstance(opt, dict):
                    label = opt.get("label")
                    if label:
                        option_labels.append(str(label))
                elif isinstance(opt, str):
                    option_labels.append(opt)
            if option_labels:
                value = f"{value}\n\n{', '.join(option_labels)}"

    if value:
        return value

    # For send_card rows: use the precedence from chat-sdk-bridge.ts line 469.
    # fallbackText > card.description > card.title
    fallback = str(content.get("fallbackText") or "").strip()
    if fallback:
        return fallback

    card = content.get("card")
    if isinstance(card, dict):
        desc = str(card.get("description") or "").strip()
        if desc:
            return desc
        title = str(card.get("title") or "").strip()
        if title:
            return title

    return ""
