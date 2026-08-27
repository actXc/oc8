from __future__ import annotations

import uuid

from oc8.hooks.executor import InProcessExecutor
from oc8.hooks.types import HookCtx, HookHandler


async def test_in_process_filter_and_action() -> None:
    ex = InProcessExecutor()
    ctx = HookCtx(tenant_id=uuid.uuid4())
    h = HookHandler("p", "task.before_create", 10, False, lambda c, d: {**d, "seen": True}, None)
    out = await ex.run_filter(h, ctx, {"title": "x"})
    assert out["seen"] is True

    hits: list[str] = []
    a = HookHandler(
        "p", "handoff.status.changed", 10, False, lambda c, **k: hits.append(k["status"]), None
    )
    await ex.run_action(a, ctx, {"status": "completed"})
    assert hits == ["completed"]
