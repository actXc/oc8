"""Holds per-tenant WebSocket sets and one Redis Pub/Sub subscriber task per
tenant. A message on a tenant's channel is fanned out ONLY to that tenant's
sockets -- the security-critical isolation invariant."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Protocol

import redis.asyncio as redis

from oc8.realtime.bus import channel_for

logger = logging.getLogger(__name__)

# A subscriber that dies on a transient Redis error resubscribes so its tenant
# doesn't go permanently silent; the backoff (seconds) keeps a persistently-down
# Redis from turning that into a busy loop.
_RESUBSCRIBE_BACKOFF_BASE = 1.0
_RESUBSCRIBE_BACKOFF_MAX = 30.0


class SupportsSend(Protocol):
    async def send_text(self, data: str) -> None: ...


class ConnectionManager:
    def __init__(self, url: str) -> None:
        self._client: redis.Redis = redis.from_url(url, decode_responses=True)
        self._conns: dict[uuid.UUID, set[SupportsSend]] = {}
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}

    async def connect(self, tenant_id: uuid.UUID, ws: SupportsSend) -> None:
        first = tenant_id not in self._conns
        self._conns.setdefault(tenant_id, set()).add(ws)
        if first:
            ready = asyncio.Event()
            self._tasks[tenant_id] = asyncio.create_task(
                self._run_subscriber(tenant_id, ready)
            )
            # Only return once the subscription is actually live, so the WS
            # handshake ("realtime.connected") is accurate and no event
            # published right after connect() can be missed. A second
            # connection to an already-subscribed tenant skips this entirely
            # (the subscription is already live).
            try:
                await asyncio.wait_for(ready.wait(), timeout=5.0)
            except TimeoutError:
                logger.warning(
                    "subscription for tenant %s not confirmed live within 5s", tenant_id
                )

    async def disconnect(self, tenant_id: uuid.UUID, ws: SupportsSend) -> None:
        conns = self._conns.get(tenant_id)
        if conns is None:
            return
        conns.discard(ws)
        if not conns:
            self._conns.pop(tenant_id, None)
            task = self._tasks.pop(tenant_id, None)
            if task is not None:
                task.cancel()

    def subscriber_count(self) -> int:
        return len(self._tasks)

    async def _broadcast(self, tenant_id: uuid.UUID, text: str) -> None:
        dead: list[SupportsSend] = []
        for ws in list(self._conns.get(tenant_id, set())):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(tenant_id, ws)

    async def _run_subscriber(
        self, tenant_id: uuid.UUID, ready: asyncio.Event | None = None
    ) -> None:
        # Resubscribe on a transient failure so the tenant self-heals instead of
        # going permanently silent (the previous version returned on the first
        # error, leaving _tasks stale and every future connect for that tenant
        # without a subscriber). The loop ends only when the tenant has no
        # sockets left or the task is cancelled (last disconnect / close).
        backoff = _RESUBSCRIBE_BACKOFF_BASE
        while tenant_id in self._conns:
            pubsub = self._client.pubsub()
            try:
                await pubsub.subscribe(channel_for(tenant_id))
                if ready is not None:
                    # Subscription is live; unblock connect(). Only the first
                    # successful subscribe signals it.
                    ready.set()
                    ready = None
                backoff = _RESUBSCRIBE_BACKOFF_BASE  # healthy again
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    await self._broadcast(tenant_id, str(message["data"]))
            except asyncio.CancelledError:
                await pubsub.aclose()  # type: ignore[no-untyped-call]
                raise
            except Exception:
                logger.warning(
                    "subscriber failed for tenant %s; resubscribing", tenant_id, exc_info=True
                )
                await pubsub.aclose()  # type: ignore[no-untyped-call]
                if tenant_id in self._conns:
                    await asyncio.sleep(min(backoff, _RESUBSCRIBE_BACKOFF_MAX))
                    backoff *= 2
                continue
            else:
                # listen() ended without error (unusual for pubsub); re-check
                # whether the tenant still has sockets and resubscribe if so.
                await pubsub.aclose()  # type: ignore[no-untyped-call]
        # No sockets left -> drop any stale task handle for this tenant.
        self._tasks.pop(tenant_id, None)

    async def close(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        self._tasks.clear()
        self._conns.clear()
        await self._client.aclose()
