"""Neutral run-time seam for optional Enterprise supervision implementations.

Community defaults are deliberately no-op. An Enterprise entry point scopes its
implementation without requiring an ``oc8`` -> ``oc8_enterprise`` import.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class SupervisionRunHook(Protocol):
    """Optional supervision callbacks invoked by the generic run loop."""

    async def create_anchor(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        task_id: uuid.UUID,
        task_text: str,
    ) -> object | None: ...

    async def checkpoint(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        task_id: uuid.UUID,
        anchor: object | None,
        tool_trace_delta: list[dict[str, Any]],
        tokens_since_checkpoint: int,
        force: bool,
        contains_restricted: bool = False,
    ) -> object | None: ...


class SupervisionQueryPort(Protocol):
    async def has_supervision(self, db: AsyncSession, *, agent_id: uuid.UUID) -> bool: ...


class _NoopSupervisionQueryPort:
    async def has_supervision(self, db: AsyncSession, *, agent_id: uuid.UUID) -> bool:
        return False


class _NoopSupervisionRunHook:
    async def create_anchor(self, db: AsyncSession, **kwargs: Any) -> None:
        return None

    async def checkpoint(self, db: AsyncSession, **kwargs: Any) -> None:
        return None


NOOP_SUPERVISION_RUN_HOOK: SupervisionRunHook = _NoopSupervisionRunHook()
NOOP_SUPERVISION_QUERY_PORT: SupervisionQueryPort = _NoopSupervisionQueryPort()
_run_hook: ContextVar[SupervisionRunHook] = ContextVar(
    "oc8_supervision_run_hook", default=NOOP_SUPERVISION_RUN_HOOK
)
_query_port: ContextVar[SupervisionQueryPort] = ContextVar(
    "oc8_supervision_query_port", default=NOOP_SUPERVISION_QUERY_PORT
)


def current_supervision_run_hook() -> SupervisionRunHook:
    """Return the hook for this execution context."""
    return _run_hook.get()


def current_supervision_query_port() -> SupervisionQueryPort:
    return _query_port.get()


@contextmanager
def use_supervision_runtime(
    hook: SupervisionRunHook, query_port: SupervisionQueryPort = NOOP_SUPERVISION_QUERY_PORT
) -> Iterator[None]:
    """Temporarily select a hook without leaking state across tests or tasks."""
    token = _run_hook.set(hook)
    query_token = _query_port.set(query_port)
    try:
        yield
    finally:
        _query_port.reset(query_token)
        _run_hook.reset(token)


def use_supervision_run_hook(hook: SupervisionRunHook) -> AbstractContextManager[None]:
    return use_supervision_runtime(hook)


async def maybe_create_anchor(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    task_id: uuid.UUID,
    task_text: str,
) -> object | None:
    """Delegate optional anchor creation to the selected implementation."""
    return await current_supervision_run_hook().create_anchor(
        db, tenant_id=tenant_id, agent_id=agent_id, task_id=task_id, task_text=task_text
    )


async def maybe_checkpoint(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    task_id: uuid.UUID,
    anchor: object | None,
    tool_trace_delta: list[dict[str, Any]],
    tokens_since_checkpoint: int,
    force: bool,
    contains_restricted: bool = False,
) -> object | None:
    """Delegate an optional checkpoint to the selected implementation."""
    return await current_supervision_run_hook().checkpoint(
        db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        task_id=task_id,
        anchor=anchor,
        tool_trace_delta=tool_trace_delta,
        tokens_since_checkpoint=tokens_since_checkpoint,
        force=force,
        contains_restricted=contains_restricted,
    )
