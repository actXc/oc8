"""Dispatch Claude-format lifecycle hooks for enabled capas."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from oc8.capas.claude_hooks.executors import ClaudeHookExecutors, HookResult, matcher_matches
from oc8.capas.claude_hooks.registry import ClaudeHookBinding, get_claude_hook_registry
from oc8.capas.manifest import ClaudeHooksSpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchResult:
    blocked: bool = False
    reason: str = ""


async def dispatch_claude_event(
    tenant_id: uuid.UUID,
    event: str,
    payload: dict[str, Any],
    *,
    tool_name: str | None = None,
) -> DispatchResult:
    """Run all registered Claude hooks for ``event``. First block wins."""
    reg = get_claude_hook_registry(tenant_id)
    for binding in reg.all_bindings():
        spec = binding.hooks
        matchers = spec.events.get(event) or []
        for matcher in matchers:
            if tool_name is not None and not matcher_matches(matcher.matcher, tool_name):
                continue
            executors = ClaudeHookExecutors(
                capa_path=binding.capa_path,
                granted_permissions=binding.granted_permissions,
                trust_level=binding.trust_level,
            )
            for action in matcher.hooks:
                try:
                    result = await executors.run(action, payload)
                except Exception:
                    logger.exception(
                        "Claude hook failed for plugin %s event %s",
                        binding.plugin_name,
                        event,
                    )
                    continue
                if result.blocked:
                    return DispatchResult(blocked=True, reason=result.reason)
    return DispatchResult()


def register_claude_hooks_for_capa(
    tenant_id: uuid.UUID,
    *,
    capa_id: str,
    plugin_name: str,
    capa_path: str,
    trust_level: str,
    granted_permissions: list[str],
    claude_hooks: dict[str, Any] | ClaudeHooksSpec | None,
) -> None:
    if not claude_hooks:
        return
    spec = (
        claude_hooks
        if isinstance(claude_hooks, ClaudeHooksSpec)
        else ClaudeHooksSpec.model_validate(claude_hooks)
    )
    if not spec.events:
        return
    get_claude_hook_registry(tenant_id).register(
        ClaudeHookBinding(
            capa_id=capa_id,
            plugin_name=plugin_name,
            capa_path=capa_path,
            trust_level=trust_level,
            granted_permissions=list(granted_permissions),
            hooks=spec,
        )
    )


def unregister_claude_hooks_for_capa(tenant_id: uuid.UUID, *, capa_id: str) -> None:
    get_claude_hook_registry(tenant_id).unregister(capa_id)
