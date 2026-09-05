"""Provider-neutral coding tools and the Toolset contract.

These schemas are what the LLM sees; `SandboxToolset` (toolset.py) executes them
inside a sandbox. Kept generic on purpose — domain tools (e.g. odoo.test) live in
agent modules, not here."""

from __future__ import annotations

from typing import Any, Protocol

from oc8.modelrouter import NeutralTool

FS_READ = "fs_read"
FS_WRITE = "fs_write"
SHELL_RUN = "shell_run"

CODING_TOOLS: list[NeutralTool] = [
    NeutralTool(
        name=FS_READ,
        description="Read a UTF-8 text file from the workspace and return its contents.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path in the sandbox."}
            },
            "required": ["path"],
        },
    ),
    NeutralTool(
        name=FS_WRITE,
        description="Write UTF-8 text to a file in the workspace, creating parent directories.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path in the sandbox."},
                "content": {"type": "string", "description": "Full file contents to write."},
            },
            "required": ["path", "content"],
        },
    ),
    NeutralTool(
        name=SHELL_RUN,
        description="Run a shell command in the workspace; returns exit code and combined output.",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string", "description": "Shell command line."}},
            "required": ["command"],
        },
    ),
]


# The coding toolset is not an MCP connection, so it needs its own frame key and
# right classification. Same fail-closed rule: anything unlisted is a write.
CODING_FRAME_KEY = "coding"
CODING_TOOL_RIGHTS: dict[str, str] = {
    "fs_read": "read",
    "fs_write": "modify",
    "shell_run": "modify",
}


class Toolset(Protocol):
    tools: list[NeutralTool]

    async def call(self, name: str, arguments: dict[str, Any]) -> str: ...
