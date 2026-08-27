"""Static contract for the Community runtime-provisioner Compose boundary."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"


def _service_block(compose: str, service: str) -> str:
    marker = f"  {service}:\n"
    start = compose.index(marker)
    next_service = re.search(r"^  \S[^\n]*:\n", compose[start + len(marker) :], re.MULTILINE)
    end = len(compose) if next_service is None else start + len(marker) + next_service.start()
    return compose[start:end]


def test_runtime_provisioner_is_the_only_community_socket_mount_owner() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert compose.count("/var/run/docker.sock:/var/run/docker.sock") == 1
    provisioner = _service_block(compose, "runtime-provisioner")
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in provisioner
    session_mount = (
        "${OC8_RUNTIME_SESSION_ROOT:-/var/lib/oc8/sessions}:"
        "${OC8_RUNTIME_SESSION_ROOT:-/var/lib/oc8/sessions}:ro"
    )
    assert session_mount in provisioner
    assert "ports:" not in provisioner

    for service in ("backend", "worker"):
        block = _service_block(compose, service)
        assert "/var/run/docker.sock" not in block
        assert "runtime-provisioner:" in block


def test_community_processes_use_the_private_provisioner_driver() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "OC8_SANDBOX_DRIVER: provisioner" in compose
    assert "OC8_SANDBOX_PROVISIONER_URL:" in compose
    assert "OC8_SANDBOX_PROVISIONER_TOKEN:" in compose
    assert "OC8_SANDBOX_PROVISIONER_TOKEN:?set OC8_SANDBOX_PROVISIONER_TOKEN in .env" in compose
