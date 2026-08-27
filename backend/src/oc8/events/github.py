"""GitHub webhook signature verification and event normalization."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

from oc8.events.types import InboundEvent


def verify_github_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def parse_github_event(headers: Mapping[str, str], payload: dict[str, Any]) -> InboundEvent:
    event = headers.get("X-GitHub-Event", "unknown")
    action = str(payload.get("action", ""))
    etype = f"github.{event}.{action}".rstrip(".")
    delivery_id = headers.get("X-GitHub-Delivery")
    return InboundEvent(source="github", type=etype, payload=payload, delivery_id=delivery_id)
