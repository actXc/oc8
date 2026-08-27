from __future__ import annotations

import hashlib
import hmac

from oc8.events.github import parse_github_event, verify_github_signature
from oc8.events.types import InboundEvent


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature_accepted() -> None:
    body = b'{"action":"opened"}'
    assert verify_github_signature("s3cret", body, _sign("s3cret", body)) is True


def test_bad_or_missing_signature_rejected() -> None:
    body = b'{"action":"opened"}'
    assert verify_github_signature("s3cret", body, _sign("wrong", body)) is False
    assert verify_github_signature("s3cret", body, None) is False
    assert verify_github_signature("", body, _sign("", body)) is False  # empty secret -> reject


def test_parse_event_type() -> None:
    ev = parse_github_event({"X-GitHub-Event": "issues"}, {"action": "opened", "n": 1})
    assert ev == InboundEvent(
        source="github", type="github.issues.opened", payload={"action": "opened", "n": 1}
    )


def test_parse_event_lifts_delivery_id() -> None:
    ev = parse_github_event(
        {"X-GitHub-Event": "issues", "X-GitHub-Delivery": "abc-123"},
        {"action": "opened"},
    )
    assert ev.delivery_id == "abc-123"


def test_parse_event_missing_delivery_id_is_none() -> None:
    ev = parse_github_event({"X-GitHub-Event": "issues"}, {"action": "opened"})
    assert ev.delivery_id is None
