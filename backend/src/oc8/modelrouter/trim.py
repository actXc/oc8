"""Keep a conversation inside the model's context window.

A long-running agent grows its transcript every turn, and once it passes the
window the run does not degrade -- it dies. The provider answers with a
complaint about a NEGATIVE max_tokens (live, 2026-07-28: `got -7075`, i.e. the
prompt was ~7000 tokens past what the model can hold), and no retry can help,
because the next attempt sends the same transcript.

What may be dropped is the MIDDLE. The system message is the agent's whole
instruction set, and the last turns are what it is doing right now; losing
either changes the answer rather than shortening the input. The drop is
announced in the transcript, because a model that silently lost its own history
will contradict itself with complete confidence.

This changes what the model sees, so it is deliberately a last resort: nothing
happens at all while the conversation fits.
"""

from __future__ import annotations

from oc8.modelrouter.http_errors import _NEGATIVE_MAX_TOKENS
from oc8.modelrouter.streaming import estimate_tokens
from oc8.modelrouter.types import NeutralMessage

#: Said in the transcript itself, in the place the dropped turns were, so the
#: model can tell the difference between "this never happened" and "I no longer
#: have it".
NOTICE = (
    "[Hinweis: Der Verlauf wurde gekürzt, weil er nicht mehr in das Kontextfenster "
    "passt. Ältere Schritte fehlen. Verlasse dich auf die letzten Nachrichten und "
    "auf das, was du im System nachlesen kannst — rate nicht.]"
)


def _cost(message: NeutralMessage) -> int:
    text = message.content or ""
    for call in message.tool_calls:
        text += call.name + str(call.arguments)
    return estimate_tokens(text) + 4  # per-message overhead the wire format adds


def trim_to_budget(
    messages: list[NeutralMessage], *, budget_tokens: int
) -> list[NeutralMessage]:
    """Drop the oldest middle turns until the conversation fits `budget_tokens`.

    Returns the input unchanged when it already fits -- the common case must not
    be touched, or a trim that fires when it need not would quietly change what
    every agent sees.
    """
    if budget_tokens <= 0 or not messages:
        return messages
    total = sum(_cost(m) for m in messages)
    if total <= budget_tokens:
        return messages

    head = [m for m in messages if m.role == "system"]
    rest = [m for m in messages if m.role != "system"]

    notice = NeutralMessage(role="system", content=NOTICE)
    floor = sum(_cost(m) for m in head) + _cost(notice)

    # Newest first: what the agent is doing right now survives, history goes.
    kept_reversed: list[NeutralMessage] = []
    running = floor
    for message in reversed(rest):
        cost = _cost(message)
        if running + cost > budget_tokens and kept_reversed:
            break
        running += cost
        kept_reversed.append(message)
    kept = list(reversed(kept_reversed))

    # A tool result whose call did not survive refers to nothing, which providers
    # reject outright -- the same shape the nameless-call guard exists for.
    surviving_calls = {call.id for m in kept for call in m.tool_calls}
    kept = [
        m
        for m in kept
        if m.role != "tool" or (m.tool_call_id or "") in surviving_calls
    ]
    return [*head, notice, *kept]


def overflow_tokens(error: str) -> int | None:
    """How many tokens a request was over the model's window, if it said so.

    Providers that clamp `max_tokens` to what is left of the window reject the
    request with the negative result, and that number IS the deficit. Reading it
    is what lets the trim calibrate itself: nobody has to configure a context
    window per model, and the endpoint we hit does not publish one (checked --
    /v1/models reports ids and nothing else).
    """
    match = _NEGATIVE_MAX_TOKENS.search(error)
    return int(match.group(1)) if match else None


def trim_for_overflow(
    messages: list[NeutralMessage], *, over_by: int, headroom: int
) -> list[NeutralMessage] | None:
    """Drop enough of the middle to clear an overflow of `over_by` tokens.

    `headroom` is the answer the request also asked for -- the deficit is
    measured against a request that wanted room to reply, so both have to fit
    afterwards. Returns None when there is nothing left to drop, so a caller
    reports the original error rather than retrying an identical request.
    """
    total = sum(_cost(m) for m in messages)
    budget = total - over_by - headroom
    if budget <= 0:
        return None
    trimmed = trim_to_budget(messages, budget_tokens=budget)
    return trimmed if len(trimmed) < len(messages) else None
