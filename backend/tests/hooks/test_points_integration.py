"""Enabling a first_party plugin with a task.before_create filter mutates the
payload the core dispatch produces. Uses InProcessExecutor (no container)."""

from __future__ import annotations

import uuid

from oc8.hooks.bus import dispatch_filter
from oc8.hooks.executor import InProcessExecutor
from oc8.hooks.points import register_core_points
from oc8.hooks.registry import HookRegistry, get_hook_registry
from oc8.hooks.types import HookCtx, HookHandler


async def test_core_point_declared_and_filter_runs() -> None:
    reg = HookRegistry()
    register_core_points(reg)
    assert reg.point("task.before_create") is not None
    pdp = reg.point("pdp.tool.authorize")
    assert pdp is not None and pdp.replaceable is False

    reg.register_handler(
        HookHandler(
            "p", "task.before_create", 10, False, lambda c, d: {**d, "tagged": "p"}, None
        )
    )
    ctx = HookCtx(tenant_id=uuid.uuid4())
    out = await dispatch_filter(
        ctx,
        "task.before_create",
        {"title": "t"},
        executor_default=InProcessExecutor(),
        registry=reg,
    )
    assert out["tagged"] == "p"


def test_all_core_points_declared() -> None:
    reg = HookRegistry()
    register_core_points(reg)
    assert reg.point("handoff.status.changed") is not None
    assert reg.point("supervision.checkpoint.recorded") is not None
    audit = reg.point("audit.event.write")
    metering = reg.point("metering.usage.record")
    assert audit is not None and audit.replaceable is False
    assert metering is not None and metering.replaceable is False


def test_register_core_points_is_idempotent() -> None:
    reg = HookRegistry()
    register_core_points(reg)
    register_core_points(reg)  # must not raise / must not duplicate handler lists
    assert reg.point("task.before_create") is not None


async def test_get_hook_registry_is_tenant_scoped_not_shared() -> None:
    """Regression for the cross-tenant dispatch bug: get_hook_registry() used
    to be a single process-global singleton, so Tenant A enabling a
    community plugin with a task.before_create handler would also fire that
    handler (and mint a scoped token under Tenant A's plugin_id/grants) on
    Tenant B's dispatch. get_hook_registry(tenant_id) must return separate
    registry instances per tenant, and dispatch_filter/dispatch_action's
    default resolution (registry or get_hook_registry(ctx.tenant_id)) must
    only ever see the dispatching tenant's own handlers."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    reg_a = get_hook_registry(tenant_a)
    reg_b = get_hook_registry(tenant_b)
    assert reg_a is not reg_b

    reg_a.register_handler(
        HookHandler(
            "tenant-a-plugin",
            "task.before_create",
            10,
            False,
            lambda c, d: {**d, "leaked_from": "tenant-a"},
            None,
        )
    )

    # Tenant B dispatches with no explicit `registry=` -- exactly how the 3
    # real call sites (task creation, handoff status change, supervision
    # checkpoint) invoke dispatch_filter/dispatch_action.
    out = await dispatch_filter(
        HookCtx(tenant_id=tenant_b),
        "task.before_create",
        {"title": "tenant b's task"},
        executor_default=InProcessExecutor(),
    )
    assert "leaked_from" not in out
    assert out == {"title": "tenant b's task"}

    # Sanity: tenant A's own dispatch still sees its own handler.
    out_a = await dispatch_filter(
        HookCtx(tenant_id=tenant_a),
        "task.before_create",
        {"title": "tenant a's task"},
        executor_default=InProcessExecutor(),
    )
    assert out_a["leaked_from"] == "tenant-a"
