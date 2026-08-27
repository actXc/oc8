"""Validation for sandbox bind mounts.

A mount is the one way a sandbox can reach the host filesystem, so it is checked
against a single allowed root -- by RESOLVED path, because `..` and a symlink
both defeat a textual prefix test.
"""

from __future__ import annotations

import os

from oc8.sandbox.types import BindMount, SandboxError


def validate_mounts(mounts: list[BindMount], *, allowed_root: str) -> list[BindMount]:
    """Return ``mounts`` unchanged, or raise if any of them escapes the root."""
    root = os.path.realpath(allowed_root)
    for mount in mounts:
        if not os.path.isabs(mount.container_path):
            raise SandboxError(f"container path must be absolute: {mount.container_path!r}")
        resolved = os.path.realpath(mount.host_path)
        if resolved != root and not resolved.startswith(root + os.sep):
            raise SandboxError(f"mount {mount.host_path!r} resolves outside {root!r}")
    return mounts
