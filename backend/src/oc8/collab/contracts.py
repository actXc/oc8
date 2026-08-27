"""Department collaboration contracts: emits/intakes on the department frame,
and payload mapping for event->handoff bindings (§14a.3)."""

from __future__ import annotations

from typing import Any


class ContractViolation(ValueError):
    """An emit or intake was attempted that the department does not declare."""


def department_emits(frame: dict[str, Any]) -> list[str]:
    emits = frame.get("emits", [])
    return list(emits) if isinstance(emits, list) else []


def department_intake(frame: dict[str, Any], handoff_type_name: str) -> dict[str, Any] | None:
    for entry in frame.get("intakes", []):
        if isinstance(entry, dict) and entry.get("handoff_type") == handoff_type_name:
            return entry
    return None


def apply_payload_map(payload_map: dict[str, str], event_payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for out_key, path in payload_map.items():
        cleaned = path[2:] if path.startswith("$.") else path.lstrip("$.")
        value: Any = event_payload
        for part in cleaned.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                value = None
                break
        out[out_key] = value
    return out
