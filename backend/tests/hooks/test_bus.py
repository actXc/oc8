from __future__ import annotations

import uuid

from oc8.hooks.bus import RateLimiter, dispatch_action, dispatch_filter
from oc8.hooks.registry import HookRegistry
from oc8.hooks.types import HookCtx, HookExecutor, HookHandler


class InProc(HookExecutor):
    async def run_filter(self, handler, ctx, data):
        assert handler.fn is not None
        return handler.fn(ctx, data)

    async def run_action(self, handler, ctx, kwargs):
        assert handler.fn is not None
        handler.fn(ctx, **kwargs)


async def test_filter_threads_in_priority_order() -> None:
    reg = HookRegistry()
    reg.declare_point("f", "filter")
    reg.register_handler(HookHandler("a", "f", 20, False, lambda c, d: [*d, "a"], None))
    reg.register_handler(HookHandler("b", "f", 10, False, lambda c, d: [*d, "b"], None))
    ctx = HookCtx(tenant_id=uuid.uuid4())
    out = await dispatch_filter(ctx, "f", [], executor_default=InProc(), registry=reg)
    assert out == ["b", "a"]


async def test_failing_handler_isolated_and_reported() -> None:
    reg = HookRegistry()
    reg.declare_point("f", "filter")

    def boom(c, d):
        raise RuntimeError("bad")

    reg.register_handler(HookHandler("bad", "f", 10, False, boom, None))
    reg.register_handler(HookHandler("ok", "f", 20, False, lambda c, d: [*d, "ok"], None))
    failed: list[str] = []

    async def on_failure(pid: str) -> None:
        failed.append(pid)

    ctx = HookCtx(tenant_id=uuid.uuid4())
    out = await dispatch_filter(
        ctx, "f", [], executor_default=InProc(), registry=reg, on_failure=on_failure
    )
    assert out == ["ok"]
    assert failed == ["bad"]


async def test_action_exceptions_isolated() -> None:
    reg = HookRegistry()
    reg.declare_point("a", "action")
    seen: list[str] = []
    reg.register_handler(
        HookHandler("x", "a", 10, False, lambda c, **k: seen.append("x"), None)
    )
    ctx = HookCtx(tenant_id=uuid.uuid4())
    await dispatch_action(ctx, "a", executor_default=InProc(), registry=reg, note="hi")
    assert seen == ["x"]


async def test_action_failing_handler_isolated_and_reported() -> None:
    reg = HookRegistry()
    reg.declare_point("a", "action")
    seen: list[str] = []

    def boom(c, **k):
        raise RuntimeError("bad")

    reg.register_handler(HookHandler("bad", "a", 10, False, boom, None))
    reg.register_handler(
        HookHandler("ok", "a", 20, False, lambda c, **k: seen.append("ok"), None)
    )
    failed: list[str] = []

    async def on_failure(pid: str) -> None:
        failed.append(pid)

    ctx = HookCtx(tenant_id=uuid.uuid4())
    await dispatch_action(
        ctx, "a", executor_default=InProc(), registry=reg, on_failure=on_failure, note="hi"
    )
    assert seen == ["ok"]
    assert failed == ["bad"]


async def test_filter_replace_short_circuits_remaining_defaults() -> None:
    reg = HookRegistry()
    reg.declare_point("f", "filter", replaceable=True)
    reg.register_handler(
        HookHandler("default", "f", 20, False, lambda c, d: [*d, "default"], None)
    )
    reg.register_handler(
        HookHandler("replacer", "f", 10, True, lambda c, d: [*d, "replacer"], None)
    )
    ctx = HookCtx(tenant_id=uuid.uuid4())
    out = await dispatch_filter(ctx, "f", [], executor_default=InProc(), registry=reg)
    assert out == ["replacer"]


async def test_rate_limited_handler_is_skipped() -> None:
    reg = HookRegistry()
    reg.declare_point("f", "filter")
    reg.register_handler(HookHandler("blocked", "f", 10, False, lambda c, d: [*d, "blocked"], None))
    reg.register_handler(HookHandler("allowed", "f", 20, False, lambda c, d: [*d, "allowed"], None))

    class BlockOne(RateLimiter):
        def allow(self, plugin_id: str) -> bool:
            return plugin_id != "blocked"

    ctx = HookCtx(tenant_id=uuid.uuid4())
    out = await dispatch_filter(
        ctx, "f", [], executor_default=InProc(), registry=reg, limiter=BlockOne()
    )
    assert out == ["allowed"]
