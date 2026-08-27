"""Execute the coding tools inside a sandbox. Same .tools/.call shape the engine
already consumes from McpSession, so run_agent can drive either."""

from __future__ import annotations

from typing import Any

from oc8.coding.tools import CODING_TOOLS, FS_READ, FS_WRITE, SHELL_RUN
from oc8.modelrouter import NeutralTool
from oc8.sandbox import SandboxDriver, SandboxHandle
from oc8.sandbox.docker_driver import DEFAULT_EXEC_TIMEOUT


class SandboxToolset:
    def __init__(
        self,
        driver: SandboxDriver,
        handle: SandboxHandle,
        *,
        exec_timeout: float = DEFAULT_EXEC_TIMEOUT,
    ) -> None:
        self._driver = driver
        self._handle = handle
        self._timeout = exec_timeout
        self.tools: list[NeutralTool] = CODING_TOOLS

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            if name == FS_READ:
                data = await self._driver.fs_read(self._handle, str(arguments["path"]))
                return data.decode("utf-8", errors="replace")
            if name == FS_WRITE:
                path = str(arguments["path"])
                content = str(arguments["content"]).encode("utf-8")
                await self._driver.fs_write(self._handle, path, content)
                return f"wrote {path} ({len(content)} bytes)"
            if name == SHELL_RUN:
                command = str(arguments["command"])
                result = await self._driver.exec(
                    self._handle, ["sh", "-lc", command], timeout=self._timeout
                )
                return f"exit {result.exit_code}\n{result.output}"
            return f"ERROR: unknown tool: {name}"
        except Exception as exc:  # surface to the model, never break the loop
            return f"ERROR: {exc}"
