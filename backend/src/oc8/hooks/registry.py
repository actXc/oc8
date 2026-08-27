"""Hook registry: core declares points; plugins register handlers. Security-
relevant namespaces are non-replaceable (§13.4.5).

Registries are scoped **per tenant** (see ``get_hook_registry``) -- a single
process-global registry would let one tenant's plugin handlers fire on
another tenant's dispatch (cross-tenant data leak + token confusion, since
the sandboxed executor mints a scoped token from the dispatching ctx's
tenant_id but the handler's own plugin_id/grants)."""

from __future__ import annotations

import uuid

from oc8.hooks.types import HookHandler, HookKind, HookPoint

PROTECTED_NAMESPACES = ("pdp.", "audit.", "metering.")


class HookSecurityError(RuntimeError):
    pass


def _is_protected(name: str) -> bool:
    return any(name.startswith(ns) for ns in PROTECTED_NAMESPACES)


class HookRegistry:
    def __init__(self) -> None:
        self._points: dict[str, HookPoint] = {}
        self._handlers: dict[str, list[HookHandler]] = {}

    def declare_point(self, name: str, kind: HookKind, *, replaceable: bool = False) -> None:
        # Protected namespaces can never be replaceable, regardless of request.
        effective = replaceable and not _is_protected(name)
        self._points[name] = HookPoint(name=name, kind=kind, replaceable=effective)
        self._handlers.setdefault(name, [])

    def point(self, name: str) -> HookPoint | None:
        return self._points.get(name)

    def register_handler(self, handler: HookHandler) -> None:
        pt = self._points.get(handler.point)
        if pt is None:
            raise HookSecurityError(f"unknown hook point {handler.point!r}")
        if handler.replace and not pt.replaceable:
            raise HookSecurityError(
                f"hook point {handler.point!r} is not replaceable"
            )
        self._handlers[handler.point].append(handler)
        self._handlers[handler.point].sort(key=lambda h: h.priority)

    def unregister_plugin(self, plugin_id: str) -> list[HookHandler]:
        """Remove all handlers for ``plugin_id`` and return the ones removed,
        so callers can tear down any sandboxed executors they held."""
        removed: list[HookHandler] = []
        for name in self._handlers:
            keep: list[HookHandler] = []
            for h in self._handlers[name]:
                if h.plugin_id == plugin_id:
                    removed.append(h)
                else:
                    keep.append(h)
            self._handlers[name] = keep
        return removed

    def handlers_for(self, name: str) -> list[HookHandler]:
        return list(self._handlers.get(name, []))


_registries: dict[uuid.UUID, HookRegistry] = {}


def get_hook_registry(tenant_id: uuid.UUID) -> HookRegistry:
    """Return the tenant-scoped registry, creating (and declaring core
    points into) it on first access. Each tenant gets its own isolated
    registry instance -- there is no shared/global registry."""
    if tenant_id not in _registries:
        reg = HookRegistry()
        from oc8.hooks.points import register_core_points  # local: avoid points<->registry cycle

        register_core_points(reg)
        _registries[tenant_id] = reg
    return _registries[tenant_id]
