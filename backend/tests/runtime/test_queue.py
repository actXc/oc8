from __future__ import annotations

import uuid
from typing import Any

import pytest
import redis.asyncio as redis

from oc8.runtime.queue import RunQueue

pytestmark = pytest.mark.asyncio


async def test_enqueue_then_dequeue_roundtrip(redis_url: str) -> None:
    q = RunQueue(redis_url, key=f"test:{uuid.uuid4()}")
    run_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    try:
        await q.enqueue(run_id=run_id, tenant_id=tenant_id)
        msg = await q.dequeue(timeout=5.0)
        assert msg is not None
        assert msg["run_id"] == str(run_id)
        assert msg["tenant_id"] == str(tenant_id)
        assert msg["redelivered"] is False
        assert isinstance(msg["entry_id"], str)
        assert msg["entry_id"] != ""
    finally:
        await q.close()


async def test_dequeue_times_out_when_empty(redis_url: str) -> None:
    q = RunQueue(redis_url, key=f"test:{uuid.uuid4()}")
    try:
        assert await q.dequeue(timeout=1.0) is None
    finally:
        await q.close()


async def test_dequeue_empty_returns_none_at_default_timeout(redis_url: str) -> None:
    """Regression test for a block-expiry on xreadgroup being misreported as
    a socket read timeout by redis-py's asyncio connection layer at larger
    timeout values. This blocks for the full ~5s window against real Redis --
    that's the price of pinning the bug at the exact timeout the continuous
    worker uses by default (run_worker's dequeue(timeout=5.0) call), so keep
    this as the only 5s-timeout case."""
    q = RunQueue(redis_url, key=f"test:{uuid.uuid4()}")
    try:
        assert await q.dequeue(timeout=5.0) is None
    finally:
        await q.close()


async def test_unacked_entry_is_reclaimed(redis_url: str) -> None:
    q = RunQueue(redis_url, key=f"test:{uuid.uuid4()}")
    run_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    try:
        await q.enqueue(run_id=run_id, tenant_id=tenant_id)
        msg = await q.dequeue(timeout=5.0)
        assert msg is not None
        # deliberately don't ack

        claimed = await q.claim_stale(min_idle_ms=0)
        assert len(claimed) == 1
        assert claimed[0]["run_id"] == str(run_id)
        assert claimed[0]["redelivered"] is True
    finally:
        await q.close()


async def test_acked_entry_is_never_reclaimed(redis_url: str) -> None:
    q = RunQueue(redis_url, key=f"test:{uuid.uuid4()}")
    run_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    try:
        await q.enqueue(run_id=run_id, tenant_id=tenant_id)
        msg = await q.dequeue(timeout=5.0)
        assert msg is not None
        await q.ack(msg["entry_id"])

        claimed = await q.claim_stale(min_idle_ms=0)
        assert claimed == []
    finally:
        await q.close()


async def _pending(raw: redis.Redis, key: str) -> list[dict[str, Any]]:
    """The group's pending entries, each with its consumer, idle time
    (`time_since_delivered`) and delivery count."""
    entries: list[dict[str, Any]] = await raw.xpending_range(
        key, "workers", min="-", max="+", count=10
    )
    return entries


async def test_touch_holds_a_claim_a_rival_would_otherwise_take(redis_url: str) -> None:
    """An entry's idle time is reset by nothing but XACK/XCLAIM, so it measures
    silence from its consumer -- never the age of the work. A consumer still
    working it says so with touch(); until 2026-08-02 nothing did, and run
    019fc303 was reclaimed and failed as "lease lost" five minutes in, with its
    container still Up."""
    key = f"test:{uuid.uuid4()}"
    q = RunQueue(redis_url, key=key, consumer="worker-1")
    rival = RunQueue(redis_url, key=key, consumer="worker-2")
    raw: redis.Redis = redis.from_url(redis_url, decode_responses=True)
    try:
        await q.enqueue(run_id=uuid.uuid4(), tenant_id=uuid.uuid4())
        msg = await q.dequeue(timeout=5.0)
        assert msg is not None
        # An hour of silence -- far past any window a rival could be using.
        await raw.xclaim(key, "workers", "worker-1", 0, [msg["entry_id"]], idle=3_600_000)
        before = await _pending(raw, key)
        assert before[0]["time_since_delivered"] >= 3_600_000

        assert await q.touch(msg["entry_id"]) is True

        after = await _pending(raw, key)
        assert after[0]["time_since_delivered"] < 60_000  # the idle clock is back at zero
        # Still ours, still pending, and NOT redelivered: JUSTID leaves the
        # delivery counter alone, so a renewal never looks like a retry.
        assert after[0]["consumer"] == "worker-1"
        assert after[0]["times_delivered"] == before[0]["times_delivered"]
        assert await rival.claim_stale(min_idle_ms=60_000) == []
    finally:
        await raw.aclose()
        await rival.close()
        await q.close()


async def test_a_claim_nobody_renews_is_taken(redis_url: str) -> None:
    """The safety net, at the level of the primitive: the same entry, the same
    hour of silence, no touch -- a rival takes it. Silence still means death;
    only the definition of silence changed."""
    key = f"test:{uuid.uuid4()}"
    q = RunQueue(redis_url, key=key, consumer="worker-1")
    rival = RunQueue(redis_url, key=key, consumer="worker-2")
    raw: redis.Redis = redis.from_url(redis_url, decode_responses=True)
    run_id = uuid.uuid4()
    try:
        await q.enqueue(run_id=run_id, tenant_id=uuid.uuid4())
        msg = await q.dequeue(timeout=5.0)
        assert msg is not None
        await raw.xclaim(key, "workers", "worker-1", 0, [msg["entry_id"]], idle=3_600_000)

        claimed = await rival.claim_stale(min_idle_ms=60_000)
        assert [c["run_id"] for c in claimed] == [str(run_id)]
        assert claimed[0]["redelivered"] is True
    finally:
        await raw.aclose()
        await rival.close()
        await q.close()


async def test_touch_says_when_the_lease_is_really_gone(redis_url: str) -> None:
    """False is the one honest answer to "do I still hold this": the entry was
    acked -- by us, or by whoever reclaimed it while we were quiet."""
    key = f"test:{uuid.uuid4()}"
    q = RunQueue(redis_url, key=key, consumer="worker-1")
    try:
        await q.enqueue(run_id=uuid.uuid4(), tenant_id=uuid.uuid4())
        msg = await q.dequeue(timeout=5.0)
        assert msg is not None
        await q.ack(msg["entry_id"])

        assert await q.touch(msg["entry_id"]) is False
        # An id that was never in the stream is the same answer, not an error.
        assert await q.touch("1-1") is False
    finally:
        await q.close()


async def test_two_consumers_in_one_group_split_entries(redis_url: str) -> None:
    key = f"test:{uuid.uuid4()}"
    q1 = RunQueue(redis_url, key=key, consumer="consumer-1")
    q2 = RunQueue(redis_url, key=key, consumer="consumer-2")
    run_id_1 = uuid.uuid4()
    run_id_2 = uuid.uuid4()
    try:
        await q1.enqueue(run_id=run_id_1, tenant_id=uuid.uuid4())
        await q1.enqueue(run_id=run_id_2, tenant_id=uuid.uuid4())

        msg1 = await q1.dequeue(timeout=5.0)
        msg2 = await q2.dequeue(timeout=5.0)
        assert msg1 is not None
        assert msg2 is not None

        got = {msg1["run_id"], msg2["run_id"]}
        assert got == {str(run_id_1), str(run_id_2)}
    finally:
        await q1.close()
        await q2.close()


async def test_group_created_before_worker_reads_history(redis_url: str) -> None:
    key = f"test:{uuid.uuid4()}"
    run_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    # Write directly to the stream via a raw client, bypassing RunQueue
    # entirely -- so the entry lands with NO consumer group in existence yet.
    # (RunQueue.enqueue() always calls _ensure_group() before xadd(), so it
    # can never reproduce this ordering on a fresh key; only a raw producer
    # can put a message in the stream before any group exists.)
    raw = redis.from_url(redis_url, decode_responses=True)
    try:
        await raw.xadd(key, {"run_id": str(run_id), "tenant_id": str(tenant_id)})

        # A fresh worker joins after the message already exists, and its
        # dequeue() is what creates the group for the first time. A group
        # created with id="$" would start after this entry and never see it;
        # id="0" must deliver it.
        worker = RunQueue(redis_url, key=key)
        try:
            msg = await worker.dequeue(timeout=5.0)
            assert msg is not None
            assert msg["run_id"] == str(run_id)
        finally:
            await worker.close()
    finally:
        await raw.aclose()


async def test_touch_on_a_trimmed_entry_is_false_and_takes_the_entry_with_it(
    redis_url: str,
) -> None:
    """The THIRD cause of False, and the one that is not a rival worker.

    Redis 7 has XCLAIM delete a pending entry whose stream entry no longer
    exists, and `enqueue` trims the stream (MAXLEN ~10_000). A run long enough to
    be lapped by ten thousand enqueues therefore has its own renewal beat destroy
    its pending entry -- after which no reclaim can ever redeliver it, and the
    run's database heartbeat is its only remaining decider. Pinned here because
    the worker used to log this as "reclaimed elsewhere", which sends an operator
    after a second worker that does not exist.
    """
    key = f"test:{uuid.uuid4()}"
    q = RunQueue(redis_url, key=key, consumer="worker-1")
    raw: redis.Redis = redis.from_url(redis_url, decode_responses=True)
    try:
        await q.enqueue(run_id=uuid.uuid4(), tenant_id=uuid.uuid4())
        msg = await q.dequeue(timeout=5.0)
        assert msg is not None
        assert len(await _pending(raw, key)) == 1
        await raw.xtrim(key, maxlen=0, approximate=False)

        assert await q.touch(msg["entry_id"]) is False
        # Not merely "not ours": gone. Nothing can reclaim it now.
        assert await _pending(raw, key) == []
    finally:
        await raw.aclose()
        await q.close()
