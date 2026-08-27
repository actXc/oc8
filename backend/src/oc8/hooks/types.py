"""Hook system types (§13.4.1)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

HookKind = Literal["filter", "action"]


@dataclass
class HookCtx:
    tenant_id: uuid.UUID
    plugin_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookPoint:
    name: str
    kind: HookKind
    replaceable: bool = False


class HookExecutor(Protocol):
    async def run_filter(self, handler: HookHandler, ctx: HookCtx, data: Any) -> Any: ...
    async def run_action(
        self, handler: HookHandler, ctx: HookCtx, kwargs: dict[str, Any]
    ) -> None: ...


@dataclass
class HookHandler:
    plugin_id: str
    point: str
    priority: int = 50
    replace: bool = False
    fn: Callable[..., Any] | None = None
    executor: HookExecutor | None = None
