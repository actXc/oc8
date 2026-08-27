from __future__ import annotations

import pytest

from oc8.hooks.registry import HookRegistry, HookSecurityError
from oc8.hooks.types import HookHandler


def test_protected_namespace_forces_non_replaceable() -> None:
    reg = HookRegistry()
    reg.declare_point("pdp.tool.authorize", "action", replaceable=True)  # requested True...
    pt = reg.point("pdp.tool.authorize")
    assert pt is not None and pt.replaceable is False  # ...forced False


def test_replace_handler_on_protected_point_rejected() -> None:
    reg = HookRegistry()
    reg.declare_point("audit.event.write", "action")
    with pytest.raises(HookSecurityError):
        reg.register_handler(
            HookHandler(
                plugin_id="p1", point="audit.event.write", priority=10,
                replace=True, fn=lambda ctx, **k: None, executor=None,
            )
        )


def test_priority_ordering_and_unregister() -> None:
    reg = HookRegistry()
    reg.declare_point("task.before_create", "filter")
    for pid, prio in [("a", 30), ("b", 10), ("c", 20)]:
        reg.register_handler(
            HookHandler(plugin_id=pid, point="task.before_create", priority=prio,
                        replace=False, fn=lambda ctx, data: data, executor=None)
        )
    order = [h.plugin_id for h in reg.handlers_for("task.before_create")]
    assert order == ["b", "c", "a"]
    reg.unregister_plugin("c")
    assert [h.plugin_id for h in reg.handlers_for("task.before_create")] == ["b", "a"]
