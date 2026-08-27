"""In-process registry mapping (source, event_type) to async handlers.

An event_type of "*" registers a wildcard handler for every event_type from
that source -- used by the Trigger Service (§8.4) to match dynamically
registered event-kind Trigger rows without one dispatcher entry per row.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from oc8.events.types import InboundEvent

logger = logging.getLogger(__name__)

Handler = Callable[[InboundEvent], Awaitable[None]]


class TriggerDispatcher:
    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], list[Handler]] = {}

    def register(self, source: str, event_type: str, handler: Handler) -> None:
        """Idempotent for the same (source, event_type, handler) triple, so a
        module-level singleton handler can be safely re-registered across
        repeated app startups (e.g. once per test's FastAPI lifespan)."""
        handlers = self._handlers.setdefault((source, event_type), [])
        if handler not in handlers:
            handlers.append(handler)

    async def dispatch(self, event: InboundEvent) -> int:
        exact = self._handlers.get((event.source, event.type), [])
        wildcard = self._handlers.get((event.source, "*"), [])
        handlers = exact + wildcard
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception("event handler failed for %s/%s", event.source, event.type)
        return len(handlers)


_dispatcher: TriggerDispatcher | None = None


def get_dispatcher() -> TriggerDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = TriggerDispatcher()
    return _dispatcher
