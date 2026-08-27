"""Per-tenant registry of enabled plugins' Claude hooks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from oc8.capas.manifest import ClaudeHooksSpec


@dataclass
class ClaudeHookBinding:
    capa_id: str
    plugin_name: str
    capa_path: str
    trust_level: str
    granted_permissions: list[str]
    hooks: ClaudeHooksSpec


@dataclass
class ClaudeHookRegistry:
    bindings: dict[str, ClaudeHookBinding] = field(default_factory=dict)

    def register(self, binding: ClaudeHookBinding) -> None:
        self.bindings[binding.capa_id] = binding

    def unregister(self, capa_id: str) -> None:
        self.bindings.pop(capa_id, None)

    def all_bindings(self) -> list[ClaudeHookBinding]:
        return list(self.bindings.values())


_registries: dict[uuid.UUID, ClaudeHookRegistry] = {}


def get_claude_hook_registry(tenant_id: uuid.UUID) -> ClaudeHookRegistry:
    if tenant_id not in _registries:
        _registries[tenant_id] = ClaudeHookRegistry()
    return _registries[tenant_id]


def reset_claude_hook_registries_for_tests() -> None:
    _registries.clear()
