"""Bind mounts are the sandbox's first hole in an otherwise sealed box.

A container that can name its own host paths can read anything the daemon can,
so every mount is validated against one allowed root -- resolved, not textual,
because `..` and a symlink both escape a prefix check.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from oc8.sandbox.mounts import validate_mounts
from oc8.sandbox.types import BindMount, SandboxError


def test_a_mount_inside_the_allowed_root_passes(tmp_path: Path) -> None:
    inside = tmp_path / "sessions" / "run-1"
    inside.mkdir(parents=True)
    mounts = [BindMount(host_path=str(inside), container_path="/workspace")]

    assert validate_mounts(mounts, allowed_root=str(tmp_path / "sessions")) == mounts


def test_a_mount_outside_the_allowed_root_is_refused(tmp_path: Path) -> None:
    (tmp_path / "sessions").mkdir()
    with pytest.raises(SandboxError, match="outside"):
        validate_mounts(
            [BindMount(host_path="/etc", container_path="/workspace")],
            allowed_root=str(tmp_path / "sessions"),
        )


def test_a_dotdot_escape_is_refused(tmp_path: Path) -> None:
    (tmp_path / "sessions").mkdir()
    with pytest.raises(SandboxError, match="outside"):
        validate_mounts(
            [BindMount(host_path=str(tmp_path / "sessions" / ".." / "secrets"),
                       container_path="/workspace")],
            allowed_root=str(tmp_path / "sessions"),
        )


def test_a_symlink_escape_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    root.mkdir()
    (tmp_path / "secrets").mkdir()
    os.symlink(tmp_path / "secrets", root / "sneaky")
    with pytest.raises(SandboxError, match="outside"):
        validate_mounts(
            [BindMount(host_path=str(root / "sneaky"), container_path="/workspace")],
            allowed_root=str(root),
        )


def test_a_relative_container_path_is_refused(tmp_path: Path) -> None:
    inside = tmp_path / "sessions" / "run-1"
    inside.mkdir(parents=True)
    with pytest.raises(SandboxError, match="absolute"):
        validate_mounts(
            [BindMount(host_path=str(inside), container_path="workspace")],
            allowed_root=str(tmp_path / "sessions"),
        )
