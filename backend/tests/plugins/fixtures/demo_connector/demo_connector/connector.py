"""A minimal Connector contributed by a plugin, used by the loader tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from oc8.knowledge.connectors.base import (
    RawDocument,
    ValidationResult,
)


class DemoConnector:
    type_id = "demo"
    config_schema: dict[str, Any] = {"type": "object", "properties": {}}
    requires_oauth: str | None = None

    async def validate(
        self, config: dict[str, Any], auth: object | None = None
    ) -> ValidationResult:
        return ValidationResult(ok=True)

    async def discover(self, config: dict[str, Any], auth: object | None = None) -> list[Any]:
        return []

    async def fetch(
        self,
        config: dict[str, Any],
        cursor: dict[str, Any] | None,
        auth: object | None = None,
    ) -> AsyncIterator[RawDocument]:
        yield RawDocument(
            source_uri="demo://1",
            title="Demo",
            content="hello from a plugin",
            content_type="text/plain",
            content_hash="deadbeef",
        )


def register(contrib: Any) -> None:
    contrib.add_connector(DemoConnector())
