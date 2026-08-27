"""End-to-end proof that Community's task and handoff core hook points dispatch
through the real service functions -- not just the unit-level bus/registry
tests in test_points_integration.py.

Each test builds a fresh HookRegistry (declared via register_core_points),
registers a handler, and monkeypatches `oc8.hooks.bus.get_hook_registry` --
the name the bus's dispatch_filter/dispatch_action resolve at call time when
the real call sites don't pass `registry=` explicitly -- so the assertions
run against real DB-backed service functions with no cross-test global
registry pollution.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.engine import run_agent
from oc8.collab.handoff import accept_handoff, create_handoff, create_handoff_type
from oc8.constants import ACME_TENANT_ID
from oc8.hooks.points import register_core_points
from oc8.hooks.registry import HookRegistry
from oc8.hooks.types import HookCtx, HookHandler
from oc8.modelrouter import CompletionResult, Usage, chunk_from_result
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


class _NoToolRouter:
    """A model router that ends the agent loop on the first turn (no tool
    calls) -- lets task.before_create be exercised without a real sandbox."""

    async def complete(self, req: Any) -> CompletionResult:
        return CompletionResult(
            text="done",
            tool_calls=[],
            usage=Usage(tokens_in=1, tokens_out=1),
            stop_reason="stop",
            provider="fake",
            model="fake",
        )

    async def stream(self, req: Any) -> Any:
        yield chunk_from_result(await self.complete(req))


def _fresh_registry() -> HookRegistry:
    reg = HookRegistry()
    register_core_points(reg)
    return reg


async def test_task_before_create_hook_observed_via_run_agent(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: _NoToolRouter())

    reg = _fresh_registry()

    def _tag_title(ctx: HookCtx, data: dict[str, Any]) -> dict[str, Any]:
        return {**data, "title": data["title"] + " [hooked]"}

    reg.register_handler(HookHandler("p1", "task.before_create", 50, False, _tag_title, None))
    monkeypatch.setattr("oc8.hooks.bus.get_hook_registry", lambda tenant_id: reg)

    async with app_session(tenant) as db:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="Hooked")
        db.add(agent)
        await db.flush()

        result = await run_agent(db, agent=agent, task_text="do the thing", tenant_id=tenant)

        task = await db.get(m.Task, result.task_id)
        assert task is not None
        assert task.title.endswith("[hooked]")


async def test_handoff_status_changed_hook_observed_via_accept_handoff(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    reg = _fresh_registry()

    hits: list[tuple[str, str]] = []

    def _record(ctx: HookCtx, **kwargs: Any) -> None:
        hits.append((kwargs["handoff_id"], kwargs["status"]))

    reg.register_handler(HookHandler("p1", "handoff.status.changed", 50, False, _record, None))
    monkeypatch.setattr("oc8.hooks.bus.get_hook_registry", lambda tenant_id: reg)

    async with app_session(tenant) as db:
        ht = await create_handoff_type(
            db,
            tenant_id=tenant,
            name="collab.svc.hook-wiring",
            payload_schema={"type": "object"},
        )
        handoff = await create_handoff(
            db,
            tenant_id=tenant,
            handoff_type=ht,
            source_department_id=uuid.uuid4(),
            target_department_id=uuid.uuid4(),
            payload={},
            created_by=uuid.uuid4(),
        )
        await accept_handoff(db, handoff)

        assert hits == [(str(handoff.id), "accepted")]
