"""Hook dispatch bus: filter threading + action fan-out, with per-handler
failure isolation and a pluggable on_failure callback (circuit-breaker)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from oc8.hooks.registry import HookRegistry, get_hook_registry
from oc8.hooks.types import HookCtx, HookExecutor, HookHandler

OnFailure = Callable[[str], Awaitable[None]]


class RateLimiter:
    """No-op default; a real token bucket can replace allow() later."""

    def allow(self, plugin_id: str) -> bool:  # pragma: no cover - trivial
        return True


_default_limiter = RateLimiter()


def _resolve(handler: HookHandler, default: HookExecutor) -> HookExecutor:
    return handler.executor or default


async def dispatch_filter(
    ctx: HookCtx,
    name: str,
    data: Any,
    *,
    executor_default: HookExecutor,
    registry: HookRegistry | None = None,
    on_failure: OnFailure | None = None,
    limiter: RateLimiter | None = None,
) -> Any:
    reg = registry or get_hook_registry(ctx.tenant_id)
    lim = limiter or _default_limiter
    point = reg.point(name)
    for handler in reg.handlers_for(name):
        if not lim.allow(handler.plugin_id):
            continue
        ctx.plugin_id = handler.plugin_id
        executor = _resolve(handler, executor_default)
        try:
            data = await executor.run_filter(handler, ctx, data)
        except Exception:  # isolation: a bad handler never breaks core
            if on_failure is not None:
                await on_failure(handler.plugin_id)
            continue
        if handler.replace and point is not None and point.replaceable:
            break  # replacing handler short-circuits remaining defaults (§13.4.5)
    return data


async def dispatch_action(
    ctx: HookCtx,
    name: str,
    *,
    executor_default: HookExecutor,
    registry: HookRegistry | None = None,
    on_failure: OnFailure | None = None,
    limiter: RateLimiter | None = None,
    **kwargs: Any,
) -> None:
    reg = registry or get_hook_registry(ctx.tenant_id)
    lim = limiter or _default_limiter
    for handler in reg.handlers_for(name):
        if not lim.allow(handler.plugin_id):
            continue
        ctx.plugin_id = handler.plugin_id
        executor = _resolve(handler, executor_default)
        try:
            await executor.run_action(handler, ctx, kwargs)
        except Exception:
            if on_failure is not None:
                await on_failure(handler.plugin_id)
            continue
