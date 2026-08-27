from __future__ import annotations

import pytest

from oc8.events.dispatcher import TriggerDispatcher, get_dispatcher
from oc8.events.types import InboundEvent

pytestmark = pytest.mark.asyncio


async def test_dispatch_invokes_matching_handlers() -> None:
    d = TriggerDispatcher()
    seen: list[str] = []
    d.register("github", "github.issues.opened", lambda e: _record(seen, "a", e))
    d.register("github", "github.issues.opened", lambda e: _record(seen, "b", e))
    d.register("github", "github.issues.closed", lambda e: _record(seen, "c", e))
    n = await d.dispatch(InboundEvent("github", "github.issues.opened", {}))
    assert n == 2 and seen == ["a", "b"]


async def test_dispatch_no_match_returns_zero() -> None:
    d = TriggerDispatcher()
    assert await d.dispatch(InboundEvent("github", "github.issues.opened", {})) == 0


async def test_get_dispatcher_singleton() -> None:
    assert get_dispatcher() is get_dispatcher()


async def _record(seen: list[str], tag: str, _e: InboundEvent) -> None:
    seen.append(tag)


async def test_dispatch_invokes_wildcard_handlers_too() -> None:
    d = TriggerDispatcher()
    seen: list[str] = []
    d.register("github", "github.issues.opened", lambda e: _record(seen, "exact", e))
    d.register("github", "*", lambda e: _record(seen, "wild", e))
    n = await d.dispatch(InboundEvent("github", "github.issues.opened", {}))
    assert n == 2 and set(seen) == {"exact", "wild"}


async def test_register_is_idempotent_for_same_handler() -> None:
    d = TriggerDispatcher()
    seen: list[str] = []

    async def handler(_e: InboundEvent) -> None:
        seen.append("x")

    d.register("github", "*", handler)
    d.register("github", "*", handler)
    n = await d.dispatch(InboundEvent("github", "github.issues.opened", {}))
    assert n == 1 and seen == ["x"]
