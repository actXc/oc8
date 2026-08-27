"""Server-side validation for sandbox requests.

The provisioner is the trust boundary: callers submit a desired sandbox, but
only this module decides which image, mount and network reaches Docker.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from oc8.sandbox.types import BindMount, SandboxError, SandboxSpec


def _is_within(path: str, root: str) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except ValueError:
        return False
    return True


def _memory_bytes(value: str) -> int:
    suffixes = {"m": 1024**2, "g": 1024**3}
    normalized = value.strip().lower()
    if len(normalized) < 2 or normalized[-1] not in suffixes:
        raise SandboxError("memory limit must use m or g units")
    try:
        amount = float(normalized[:-1])
    except ValueError as exc:
        raise SandboxError("memory limit is invalid") from exc
    if amount <= 0:
        raise SandboxError("memory limit must be positive")
    return int(amount * suffixes[normalized[-1]])


@dataclass(frozen=True)
class ProvisionerPolicy:
    allowed_images: set[str]
    agent_network: str
    session_root: str
    platform_mounts: dict[str, str]
    networked_images: set[str] | None = None
    max_memory_bytes: int = 2 * 1024**3
    max_pids: int = 512
    max_cpu: float = 2.0

    def validate(self, spec: SandboxSpec) -> SandboxSpec:
        if spec.image not in self.allowed_images:
            raise SandboxError("image is not allowed")
        if _memory_bytes(spec.mem_limit) > self.max_memory_bytes:
            raise SandboxError("memory limit exceeds policy")
        if not 0 < spec.pids_limit <= self.max_pids:
            raise SandboxError("pids limit exceeds policy")
        if not 0 < spec.cpu_limit <= self.max_cpu:
            raise SandboxError("cpu limit exceeds policy")

        networked_images = self.networked_images or self.allowed_images
        if spec.network_disabled:
            if spec.network is not None:
                raise SandboxError("network must be unset when disabled")
        elif spec.image not in networked_images or spec.network != self.agent_network:
            raise SandboxError("network is not allowed")

        for mount in spec.mounts:
            self._validate_mount(mount)

        # The Docker adapter also enforces no-new-privileges and privileged=False.
        return replace(spec, cap_drop=["ALL"])

    def _validate_mount(self, mount: BindMount) -> None:
        if _is_within(mount.host_path, self.session_root):
            # Inside the session root anything goes, read-only included. The
            # boundary that matters here is the per-run, UUID-derived path: a
            # run cannot name another run's directory, and read-only only
            # narrows what it may do inside its own. Runtimes that generate a
            # per-run config file (a run-scoped token, the MCP endpoint, an
            # approval policy) mount it read-only from here on purpose, so the
            # agent's own file tools cannot rewrite how it is governed; those
            # files cannot live in `platform_mounts`, which is a static
            # startup-time registry with no per-request entries.
            return

        expected_target = self.platform_mounts.get(str(Path(mount.host_path).resolve()))
        if expected_target != mount.container_path or not mount.readonly:
            raise SandboxError("mount is not allowed")
