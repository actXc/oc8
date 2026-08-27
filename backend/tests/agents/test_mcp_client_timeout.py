"""A tool server that stops answering must cost one step, not the whole run.

WHY THIS EXISTS. On 2026-08-02 the worker stopped letting go of a run's stream
entry while it was still working it (worker._keep_claimed) -- it had to, because
the old five-minute reclaim was killing live runs. That fix removed the only
thing that ever ended a WEDGED in-process run, and it removed it from the
DEFAULT runtime: `agent_isolation` is off unless a deployment turns it on, so
`Oc8AgentRuntime` runs in the worker's own process with no container to time out
around it.

The hole was exact. `ClientSession(read, write)` and `call_tool(name, args)` both
default `read_timeout_seconds` to None -- wait for ever. A third-party MCP server
that accepted a call and never answered therefore left the run `running`, the
agent `running`, the claim renewed and the heartbeat beating (both honestly: the
task really was alive), and the worker itself blocked, since it awaits its
handler inline. Neither decider could fire, because both were being told the
truth.

These tests spawn a REAL stdio MCP server -- one that never answers a tool call,
one that never answers the handshake -- because the whole defect was a default
in someone else's library, and a mock of that library would have agreed with
whatever we assumed.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

# mcp 2.x renamed this class MCPError; 1.x has no such alias. Same open-pin
# drift `mcp_client.py`'s own `_read_timeout`/`_schema_of` shims exist for --
# a third occurrence found while regenerating this lock during the oc8 rename.
try:
    from mcp.shared.exceptions import MCPError as McpError
except ImportError:
    from mcp.shared.exceptions import McpError

from oc8.agent.mcp_client import MCP_REQUEST_TIMEOUT_SECONDS, McpSession

pytestmark = pytest.mark.asyncio

#: Short enough to wait out, long enough that a slow test host cannot beat it.
_TIMEOUT_S = 2.0
#: The whole test fails rather than hangs if the bound is ever removed again.
_PATIENCE_S = 30.0

# Same mcp 2.x rename as MCPError above, but this source runs in a fresh
# subprocess (`_server` below), so the shim has to live inside the string,
# not in this file's own imports.
_HANGS_ON_CALL = """
import time

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP

mcp = FastMCP("hangs")


@mcp.tool()
def read_the_crm() -> str:
    "Answers eventually, which is to say never."
    time.sleep(600)
    return "never"


mcp.run()
"""

#: Answers nothing at all, not even `initialize`. A server that is up but mute is
#: the shape a misconfigured or half-started bridge takes, and it used to hang
#: the run before the agent had done anything.
_HANGS_ON_HANDSHAKE = """
import sys, time

sys.stdin.read()
time.sleep(600)
"""


def _server(tmp_path: Path, source: str) -> tuple[str, list[str]]:
    script = tmp_path / "server.py"
    script.write_text(source)
    return sys.executable, [str(script)]


async def test_a_tool_call_that_is_never_answered_fails_the_call_not_the_run(
    tmp_path: Path,
) -> None:
    command, args = _server(tmp_path, _HANGS_ON_CALL)
    started = time.monotonic()

    async with asyncio.timeout(_PATIENCE_S):
        async with McpSession(command, args, timeout_s=_TIMEOUT_S) as session:
            assert [t.name for t in session.tools] == ["read_the_crm"]
            with pytest.raises(McpError) as raised:
                await session.call("read_the_crm", {})

    # mcp_client.py never catches this -- the wording is the SDK's own, and it
    # changed between majors ("Timed out ..." in 1.x, "...timed out" in 2.0.0).
    # pyproject.toml pins mcp open across both on purpose, so match either.
    assert "timed out" in str(raised.value).lower()
    # Bounded by OUR timeout, not by the test's patience.
    assert time.monotonic() - started < _PATIENCE_S / 2


async def test_a_server_that_never_completes_the_handshake_does_not_hang_the_run(
    tmp_path: Path,
) -> None:
    command, args = _server(tmp_path, _HANGS_ON_HANDSHAKE)

    async with asyncio.timeout(_PATIENCE_S):
        with pytest.raises(McpError):
            async with McpSession(command, args, timeout_s=_TIMEOUT_S):
                pass  # pragma: no cover -- __aenter__ is what must raise


async def test_the_default_is_a_bound_and_not_the_sdk_none() -> None:
    """The tests above pass an explicit short timeout so they can be waited out.
    This one is about the value production actually gets: None here is the whole
    defect, and a future edit that "simplifies" the constructor back to the SDK
    default would leave both tests above green."""
    assert MCP_REQUEST_TIMEOUT_SECONDS is not None
    assert 0 < MCP_REQUEST_TIMEOUT_SECONDS < 3600
    assert McpSession("x", [])._timeout_s == MCP_REQUEST_TIMEOUT_SECONDS
