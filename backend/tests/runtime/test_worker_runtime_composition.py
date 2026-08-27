"""Worker runtime composition is scoped to a complete job and resets safely."""

from __future__ import annotations

import uuid

import pytest

from oc8.edition.runtime import RuntimeComposition
from oc8.runtime.supervision_hook import (
    current_supervision_query_port,
    current_supervision_run_hook,
)
from oc8.runtime.worker import _process

pytestmark = pytest.mark.asyncio


class _Queue:
    async def touch(self, entry_id: str) -> bool:
        return True

    async def ack(self, entry_id: str) -> None:
        return None


class _Hook:
    async def create_anchor(self, *args: object, **kwargs: object) -> None:
        return None

    async def checkpoint(self, *args: object, **kwargs: object) -> None:
        return None


class _QueryPort:
    async def has_supervision(self, *args: object, **kwargs: object) -> bool:
        return True


async def test_worker_scopes_composition_to_entire_handler_and_resets() -> None:
    hook = _Hook()
    query_port = _QueryPort()
    seen: list[tuple[object, object]] = []

    async def handler(message: object) -> None:
        seen.append((current_supervision_run_hook(), current_supervision_query_port()))
        raise RuntimeError("expected worker failure")

    original = current_supervision_run_hook()
    original_query = current_supervision_query_port()
    await _process(
        _Queue(),
        {"entry_id": "1", "run_id": str(uuid.uuid4())},  # type: ignore[arg-type]
        handler,  # type: ignore[arg-type]
        RuntimeComposition(hook, query_port),
    )

    assert seen == [(hook, query_port)]
    assert current_supervision_run_hook() is original
    assert current_supervision_query_port() is original_query
