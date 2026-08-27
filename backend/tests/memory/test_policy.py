from __future__ import annotations

from oc8.authz.pdp import Effect
from oc8.memory.policy import authorize_memory_read, authorize_memory_write


def test_agent_tier_always_readable_and_writable() -> None:
    assert authorize_memory_read({}, {}, "agent") is True
    assert authorize_memory_write({}, {}, "agent").effect is Effect.ALLOW


def test_department_tier_read_granted_by_frame() -> None:
    frame = {"memory": {"department": ["read", "write"]}}
    assert authorize_memory_read(frame, {}, "department") is True
    assert authorize_memory_write(frame, {}, "department").effect is Effect.ALLOW


def test_department_tier_denied_when_frame_silent() -> None:
    assert authorize_memory_read({}, {}, "department") is False
    assert authorize_memory_write({}, {}, "department").effect is Effect.DENY


def test_narrowing_can_only_tighten_department_tier() -> None:
    frame = {"memory": {"department": ["read", "write"]}}
    narrowing = {"memory": {"department": ["read"]}}  # drops write
    assert authorize_memory_read(frame, narrowing, "department") is True
    assert authorize_memory_write(frame, narrowing, "department").effect is Effect.DENY


def test_narrowing_absent_key_defers_to_frame() -> None:
    frame = {"memory": {"department": ["read", "write"]}}
    assert authorize_memory_read(frame, {}, "department") is True
    assert authorize_memory_write(frame, {}, "department").effect is Effect.ALLOW


def test_company_tier_read_requires_frame_grant() -> None:
    assert authorize_memory_read({}, {}, "company") is False
    frame = {"memory": {"company": ["read"]}}
    assert authorize_memory_read(frame, {}, "company") is True


def test_company_tier_write_always_requires_approval() -> None:
    # Unconditional per §10.1 — no frame grant makes it ALLOW; it's never DENY either.
    decision = authorize_memory_write({}, {}, "company")
    assert decision.effect is Effect.REQUIRE_APPROVAL
    frame = {"memory": {"company": ["read", "write"]}}
    decision2 = authorize_memory_write(frame, {}, "company")
    assert decision2.effect is Effect.REQUIRE_APPROVAL


def test_unknown_tier_denied() -> None:
    assert authorize_memory_read({}, {}, "bogus") is False
    assert authorize_memory_write({}, {}, "bogus").effect is Effect.DENY
