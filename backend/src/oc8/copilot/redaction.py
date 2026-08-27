"""One-way redaction used before every Copilot model or persistence boundary."""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(?:secret|password|credential|token|api[_-]?key|authorization)", re.IGNORECASE
)
_ASSIGNMENT = re.compile(
    r"(?i)\b(secret|password|credential|token|api[_-]?key)\b\s*[:=]\s*[^\s,;]+"
)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")


def redact_text(value: str) -> str:
    """Remove common credential-bearing text forms without retaining the value."""
    value = _ASSIGNMENT.sub(r"\1=[redacted]", value)
    return _BEARER.sub("Bearer [redacted]", value)


def redact_value(value: Any) -> Any:
    """Recursively remove values under sensitive keys before serialization."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "[redacted]" if _SENSITIVE_KEY.search(str(key)) else redact_value(item)
            for key, item in value.items()
        }
    return value


def is_secret_request(message: str) -> bool:
    """Copilot never solicits, accepts, or forwards credential values."""
    return bool(_SENSITIVE_KEY.search(message))
