# backend/tests/sandbox/test_spec_runtime.py
from __future__ import annotations

from unittest.mock import MagicMock

from oc8.sandbox.docker_driver import DockerSandboxDriver
from oc8.sandbox.types import SandboxSpec


def test_spec_defaults_omit_runtime() -> None:
    spec = SandboxSpec(image="alpine:3")
    assert spec.runtime is None
    assert spec.read_only is False


async def test_provision_passes_runtime_and_readonly() -> None:
    client = MagicMock()
    container = MagicMock()
    container.id = "cid"
    container.exec_run.return_value.exit_code = 0
    client.containers.run.return_value = container
    driver = DockerSandboxDriver(client=client)

    await driver.provision(
        SandboxSpec(image="alpine:3", runtime="runsc", read_only=True)
    )

    kwargs = client.containers.run.call_args.kwargs
    assert kwargs["runtime"] == "runsc"
    assert kwargs["read_only"] is True


async def test_provision_omits_runtime_when_unset() -> None:
    client = MagicMock()
    container = MagicMock()
    container.id = "cid"
    container.exec_run.return_value.exit_code = 0
    client.containers.run.return_value = container
    driver = DockerSandboxDriver(client=client)

    await driver.provision(SandboxSpec(image="alpine:3"))

    kwargs = client.containers.run.call_args.kwargs
    assert "runtime" not in kwargs
    assert kwargs.get("read_only", False) is False
