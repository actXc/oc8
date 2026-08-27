from __future__ import annotations

import docker
import pytest

from oc8.sandbox import (
    DockerSandboxDriver,
    SandboxSpec,
    get_sandbox_driver,
    sandbox_session,
)


def test_get_sandbox_driver_is_singleton() -> None:
    assert get_sandbox_driver() is get_sandbox_driver()
    assert isinstance(get_sandbox_driver(), DockerSandboxDriver)


async def test_sandbox_session_provisions_and_tears_down() -> None:
    client = docker.from_env()  # type: ignore[attr-defined]
    seen_id = ""
    async with sandbox_session(SandboxSpec(image="alpine:latest")) as (driver, handle):
        seen_id = handle.container_id
        result = await driver.exec(handle, ["echo", "hi"])
        assert result.output.strip() == "hi"
    with pytest.raises(docker.errors.NotFound):
        client.containers.get(seen_id)
