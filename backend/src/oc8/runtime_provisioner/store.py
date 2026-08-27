"""Opaque sandbox-handle registry owned by the provisioner process."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from oc8.sandbox.types import SandboxHandle


@dataclass(frozen=True)
class _StoredSandbox:
    handle: SandboxHandle
    owner: str


class ProvisionerStore:
    def __init__(self) -> None:
        self._sandboxes: dict[str, _StoredSandbox] = {}

    def put(self, handle: SandboxHandle, *, owner: str) -> SandboxHandle:
        opaque_id = secrets.token_urlsafe(32)
        self._sandboxes[opaque_id] = _StoredSandbox(handle=handle, owner=owner)
        return SandboxHandle(container_id=opaque_id, image=handle.image)

    def get(self, opaque_id: str, *, owner: str) -> SandboxHandle | None:
        record = self._sandboxes.get(opaque_id)
        if record is None or record.owner != owner:
            return None
        return record.handle

    def discard(self, opaque_id: str, *, owner: str) -> None:
        record = self._sandboxes.get(opaque_id)
        if record is not None and record.owner == owner:
            del self._sandboxes[opaque_id]
