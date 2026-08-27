from __future__ import annotations

import uuid
from typing import Any

import pytest
from tests.conftest import AppSessionFactory

from oc8.audit.attribution import resolve_responsible
from oc8.audit.chain import GENESIS, _hash, append_event, verify_chain
from oc8.auth import Principal

pytestmark = pytest.mark.asyncio


def test_resolve_responsible_fallback_chain() -> None:
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    op = Principal(tenant_id=tenant, subject="alice", role="operator", kind="operator")
    # 1. operator principal wins
    assert resolve_responsible(tenant_id=tenant, actor_type="agent", actor_id=agent,
                               principal=op) == ("operator", "alice")
    # 2. originating operator (no live principal)
    assert resolve_responsible(tenant_id=tenant, actor_type="agent", actor_id=agent,
                               originating_operator="bob") == ("operator", "bob")
    # 3. agent actor
    assert resolve_responsible(tenant_id=tenant, actor_type="agent",
                               actor_id=agent) == ("agent", str(agent))
    # 4. tenant default (system/unknown, no actor id)
    assert resolve_responsible(tenant_id=tenant, actor_type="system",
                               actor_id=None) == ("tenant", str(tenant))


async def test_append_event_attributes_and_stores(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    op = Principal(tenant_id=tenant, subject="alice", role="operator", kind="operator")
    async with app_session(tenant) as s:
        ev = await append_event(s, tenant_id=tenant, actor_type="operator", actor_id=None,
                                category="admin", action="a.b", principal=op)
        assert ev.responsible_type == "operator" and ev.responsible_id == "alice"


async def test_hash_chain_old_and_new_verify(app_session: AppSessionFactory) -> None:
    """The compat crux: an event written the OLD way (no responsible, hashed
    without the keys) plus NEW attributed events verify in one chain."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        # Simulate a pre-A3 event: its hash was built from the LITERAL 8-key payload
        # (no responsible keys at all). Hardcode it INDEPENDENTLY of _event_payload --
        # if we built it via _event_payload, the test would be tautological (both write
        # and verify use the same builder, so an include-null regression would still
        # pass). This dict mirrors append_event's pre-A3 inline payload exactly, so the
        # test genuinely falsifies the omit-when-null rule: verify_chain reconstructs
        # via _event_payload with NULL responsible and MUST produce these same 8 keys.
        from oc8 import models as m
        old_payload: dict[str, Any] = {
            "tenant_id": str(tenant),
            "actor_type": "system",
            "actor_id": None,
            "category": "c",
            "action": "old",
            "resource": {},
            "decision": None,
            "reason": None,
        }
        old = m.AuditEvent(
            tenant_id=tenant, actor_type="system", actor_id=None, category="c",
            action="old", resource={}, decision=None, reason=None,
            # mac_version=0: deliberately forging a legacy unkeyed row.
            prev_hash=GENESIS, hash=_hash(GENESIS, old_payload, 0),
        )
        s.add(old)
        await s.flush()
        # New attributed events chain on top.
        await append_event(s, tenant_id=tenant, actor_type="operator", actor_id=None,
                           category="c", action="new1", principal=Principal(
                               tenant_id=tenant, subject="alice", role="operator", kind="operator"))
        await append_event(s, tenant_id=tenant, actor_type="system", actor_id=None,
                           category="c", action="new2")
        assert await verify_chain(s, tenant) is True
