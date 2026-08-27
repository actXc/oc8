from __future__ import annotations

import asyncio
import uuid

import pytest
import redis.asyncio as redis

from oc8.realtime.bus import channel_for
from oc8.realtime.manager import ConnectionManager

pytestmark = pytest.mark.asyncio


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


async def test_broadcast_only_reaches_same_tenant_sockets(redis_url: str) -> None:
    mgr = ConnectionManager(redis_url)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    a1, a2, b1 = FakeWS(), FakeWS(), FakeWS()
    await mgr.connect(tenant_a, a1)
    await mgr.connect(tenant_a, a2)
    await mgr.connect(tenant_b, b1)
    try:
        await mgr._broadcast(tenant_a, '{"type":"agent.status"}')
        assert a1.sent == ['{"type":"agent.status"}']
        assert a2.sent == ['{"type":"agent.status"}']
        assert b1.sent == []  # THE isolation invariant: B never sees A's event
    finally:
        await mgr.close()


async def test_subscriber_lifecycle(redis_url: str) -> None:
    mgr = ConnectionManager(redis_url)
    tenant = uuid.uuid4()
    a, b = FakeWS(), FakeWS()
    try:
        await mgr.connect(tenant, a)
        assert mgr.subscriber_count() == 1
        await mgr.connect(tenant, b)  # same tenant reuses the one subscriber
        assert mgr.subscriber_count() == 1
        await mgr.disconnect(tenant, a)
        assert mgr.subscriber_count() == 1  # still one socket left
        await mgr.disconnect(tenant, b)  # last one out -> subscriber cancelled
        assert mgr.subscriber_count() == 0
    finally:
        await mgr.close()


async def test_failed_send_is_isolated(redis_url: str) -> None:
    mgr = ConnectionManager(redis_url)
    tenant = uuid.uuid4()

    class BoomWS(FakeWS):
        async def send_text(self, data: str) -> None:
            raise RuntimeError("socket dead")

    good, bad = FakeWS(), BoomWS()
    await mgr.connect(tenant, good)
    await mgr.connect(tenant, bad)
    try:
        await mgr._broadcast(tenant, "hi")  # bad raises internally, must not stop `good`
        assert good.sent == ["hi"]
    finally:
        await mgr.close()


async def test_connect_returns_only_once_subscription_is_live(redis_url: str) -> None:
    """After connect() returns, an event published to the tenant channel must be
    delivered to the socket WITHOUT any settle/sleep -- proving the subscription
    is already live. This FAILS against a create-task-and-return connect() (the
    subscribe() has not run yet, so the message is missed)."""
    mgr = ConnectionManager(redis_url)
    tenant = uuid.uuid4()
    ws = FakeWS()
    publisher = redis.from_url(redis_url, decode_responses=True)
    try:
        await mgr.connect(tenant, ws)
        # No sleep/settle: if the subscription is live, this is delivered.
        await publisher.publish(channel_for(tenant), "live-now")
        for _ in range(50):
            if ws.sent:
                break
            await asyncio.sleep(0.02)
        assert ws.sent == ["live-now"]
    finally:
        await publisher.aclose()
        await mgr.close()


class _FlakyPubSub:
    """A pubsub whose FIRST listen() raises (a mid-listen Redis drop), then
    delivers queued messages on the next listen() (after the subscriber
    self-heals by resubscribing)."""

    def __init__(self) -> None:
        self.subscribe_calls = 0
        self._fail = True
        self._q: asyncio.Queue[dict[str, str]] = asyncio.Queue()

    async def subscribe(self, channel: str) -> None:
        self.subscribe_calls += 1

    async def listen(self):  # type: ignore[no-untyped-def]
        if self._fail:
            self._fail = False
            raise RuntimeError("simulated redis drop mid-listen")
        while True:
            yield await self._q.get()

    async def aclose(self) -> None:
        pass

    async def feed(self, data: str) -> None:
        await self._q.put({"type": "message", "data": data})


class _FlakyRedis:
    def __init__(self, ps: _FlakyPubSub) -> None:
        self._ps = ps

    def pubsub(self) -> _FlakyPubSub:
        return self._ps

    async def aclose(self) -> None:
        pass


async def test_subscriber_self_heals_after_redis_drop(monkeypatch: pytest.MonkeyPatch) -> None:
    import oc8.realtime.manager as mgr_mod

    monkeypatch.setattr(mgr_mod, "_RESUBSCRIBE_BACKOFF_BASE", 0.01)
    ps = _FlakyPubSub()
    mgr = ConnectionManager("redis://unused")
    mgr._client = _FlakyRedis(ps)  # type: ignore[assignment]
    tenant = uuid.uuid4()
    ws = FakeWS()
    await mgr.connect(tenant, ws)  # first listen() raises -> must resubscribe
    try:
        for _ in range(100):
            if ps.subscribe_calls >= 2:
                break
            await asyncio.sleep(0.02)
        assert ps.subscribe_calls >= 2, "subscriber did not resubscribe after a Redis drop"
        # the healed subscription must still deliver events to the socket
        await ps.feed('{"type":"agent.status","x":1}')
        for _ in range(100):
            if ws.sent:
                break
            await asyncio.sleep(0.02)
        assert ws.sent == ['{"type":"agent.status","x":1}'], "healed subscriber did not deliver"
        assert mgr.subscriber_count() == 1  # one live, self-healed task
    finally:
        await mgr.close()
