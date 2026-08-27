from __future__ import annotations

from oc8.runtime_provisioner.store import ProvisionerStore
from oc8.sandbox.types import SandboxHandle


def test_store_keeps_docker_handles_owner_bound_and_opaque() -> None:
    store = ProvisionerStore()
    opaque = store.put(SandboxHandle("docker-id", "oc8-runtime:1"), owner="tenant-a")

    assert opaque.container_id != "docker-id"
    assert store.get(opaque.container_id, owner="tenant-b") is None
    assert store.get(opaque.container_id, owner="tenant-a") == SandboxHandle(
        "docker-id", "oc8-runtime:1"
    )
