from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.collab.handoff import accept_handoff, create_handoff, create_handoff_type
from oc8.metering import set_budget, trigger_budget_hard_stop
from oc8.metering.usage import record_usage
from oc8.realtime.bus import channel_for
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

_SCHEMA = {
    "type": "object",
    "required": ["customer"],
    "properties": {"customer": {"type": "string"}},
}


async def _drain(pubsub) -> dict[str, Any] | None:  # type: ignore[no-untyped-def]
    for _ in range(20):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
        if msg is not None:
            data: dict[str, Any] = json.loads(msg["data"])
            return data
    return None


async def _drain_all(pubsub, count: int) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    envs: list[dict[str, Any]] = []
    for _ in range(40):
        if len(envs) >= count:
            break
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
        if msg is not None:
            envs.append(json.loads(msg["data"]))
    return envs


async def _make_pending_handoff(db: AsyncSession, tenant_id: uuid.UUID) -> m.Handoff:
    ht = await create_handoff_type(
        db, tenant_id=tenant_id, name="collab.svc.kickoff", payload_schema=_SCHEMA
    )
    return await create_handoff(
        db,
        tenant_id=tenant_id,
        handoff_type=ht,
        source_department_id=uuid.uuid4(),
        target_department_id=uuid.uuid4(),
        payload={"customer": "Acme"},
        created_by=uuid.uuid4(),
    )


async def test_accept_handoff_publishes_status(
    app_session: AppSessionFactory, redis_url: str
) -> None:
    tenant_id = uuid.uuid4()
    sub = redis.from_url(redis_url, decode_responses=True)
    pubsub = sub.pubsub()
    await pubsub.subscribe(channel_for(tenant_id))
    await pubsub.get_message(timeout=1.0)
    try:
        async with app_session(tenant_id) as db:
            handoff = await _make_pending_handoff(db, tenant_id)
            await accept_handoff(db, handoff)
            env = await _drain(pubsub)
        assert env is not None and env["type"] == "handoff.status"
        assert env["data"]["handoff_id"] == str(handoff.id)
        assert env["data"]["status"] == handoff.status
    finally:
        await pubsub.aclose()  # type: ignore[no-untyped-call]
        await sub.aclose()


async def test_trigger_budget_hard_stop_publishes(
    app_session: AppSessionFactory, redis_url: str
) -> None:
    tenant_id = uuid.uuid4()
    dept = uuid.uuid4()
    sub = redis.from_url(redis_url, decode_responses=True)
    pubsub = sub.pubsub()
    await pubsub.subscribe(channel_for(tenant_id))
    await pubsub.get_message(timeout=1.0)
    try:
        async with app_session(tenant_id) as db:
            await set_budget(
                db,
                tenant_id=tenant_id,
                department_id=dept,
                soft_limit_tokens=None,
                hard_limit_tokens=100,
            )
            await record_usage(
                db,
                tenant_id=tenant_id,
                department_id=dept,
                agent_id=uuid.uuid4(),
                request_id=uuid.uuid4(),
                provider="p",
                model="m",
                tokens_in=200,
                tokens_out=0,
            )
            breaching = m.Agent(
                tenant_id=tenant_id, department_id=dept, name="A", status="running"
            )
            db.add(breaching)
            await db.flush()
            incident = await trigger_budget_hard_stop(
                db, tenant_id=tenant_id, breaching_agent=breaching
            )
            assert incident is not None
            envs = await _drain_all(pubsub, 2)
        types = {e["type"] for e in envs}
        assert "approval.created" in types
        assert "agent.status" in types
        approval_env = next(e for e in envs if e["type"] == "approval.created")
        assert approval_env["data"]["action_type"] == "budget_incident"
        assert approval_env["data"]["approval_id"] == str(incident.id)
        status_env = next(e for e in envs if e["type"] == "agent.status")
        assert status_env["data"]["status"] == "paused"
    finally:
        await pubsub.aclose()  # type: ignore[no-untyped-call]
        await sub.aclose()
