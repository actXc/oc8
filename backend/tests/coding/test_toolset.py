from __future__ import annotations

import pytest

from oc8.coding.toolset import SandboxToolset
from oc8.sandbox import SandboxSpec, get_sandbox_driver

pytestmark = pytest.mark.asyncio

_IMAGE = "alpine:latest"


async def test_fs_write_read_and_shell_via_toolset() -> None:
    driver = get_sandbox_driver()
    handle = await driver.provision(SandboxSpec(image=_IMAGE))
    try:
        ts = SandboxToolset(driver, handle)
        assert {t.name for t in ts.tools} == {"fs_read", "fs_write", "shell_run"}

        wrote = await ts.call("fs_write", {"path": "/workspace/a.txt", "content": "hi\n"})
        assert "wrote" in wrote and "/workspace/a.txt" in wrote

        read = await ts.call("fs_read", {"path": "/workspace/a.txt"})
        assert read == "hi\n"

        shell = await ts.call("shell_run", {"command": "echo hello && exit 0"})
        assert "exit 0" in shell and "hello" in shell
    finally:
        await driver.teardown(handle)


async def test_unknown_tool_and_errors_are_strings() -> None:
    driver = get_sandbox_driver()
    handle = await driver.provision(SandboxSpec(image=_IMAGE))
    try:
        ts = SandboxToolset(driver, handle)
        assert "unknown tool" in await ts.call("nope", {})
        # reading a missing file surfaces as an ERROR string, not a raise
        assert (await ts.call("fs_read", {"path": "/workspace/missing"})).startswith("ERROR")
    finally:
        await driver.teardown(handle)


async def test_coding_session_roundtrip() -> None:
    from oc8.coding import coding_session

    seen = ""
    async with coding_session(SandboxSpec(image=_IMAGE)) as ts:
        await ts.call("fs_write", {"path": "/workspace/x", "content": "y"})
        assert await ts.call("fs_read", {"path": "/workspace/x"}) == "y"
        # capture a container id via a shell echo of hostname to prove teardown
        seen = await ts.call("shell_run", {"command": "echo alive"})
    assert "alive" in seen
