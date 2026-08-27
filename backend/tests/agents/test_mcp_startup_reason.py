"""A tool server that refuses to start must say WHY, not just that it closed.

WHY THIS EXISTS. Connecting the wizard to a real Odoo produced, on screen,
`Connection closed`. The Odoo server had actually answered something precise --
`403: MCP Server is disabled globally` -- and the operator read the generic
message as a credentials problem and re-entered them.

The reason was never lost, only misdelivered: `stdio_client` writes the child's
stderr to whatever it is handed and defaults to the parent's, so the sentence
that explained everything went to the container log while the screen got the
transport's own view of a pipe that ended. This pins the delivery.

A REAL subprocess, like its sibling `test_mcp_client_timeout.py`, because the
behaviour under test belongs to someone else's library and a mock of it would
agree with whatever we assumed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from oc8.agent.mcp_client import McpServerStartupError, McpSession

pytestmark = pytest.mark.asyncio

#: Exits before the handshake, after printing a reason a person can act on --
#: the shape of the Odoo failure, without needing an Odoo.
_REFUSES_TO_START = """
import sys

print("Traceback (most recent call last):", file=sys.stderr)
print('  File "/x/odoo_connection.py", line 302, in _test_connection', file=sys.stderr)
print("Error: MCP Server is disabled globally.", file=sys.stderr)
sys.exit(1)
"""

#: The real Odoo shape: an ExceptionGroup whose informative member is printed
#: FIRST, then re-raised through a wrapper whose own message says nothing.
#: Verbatim-shaped from the live failure -- taking the LAST line here is what
#: reported "unhandled errors in a TaskGroup (1 sub-exception)" to the operator
#: when the truth was a rejected Odoo password.
_WRAPS_THE_REAL_CAUSE = r"""
import sys

sys.stderr.write(
    "  + Exception Group Traceback (most recent call last):\n"
    "  |   File '/x/server.py', line 191, in _ensure_connection\n"
    "  |     self.connection.authenticate()\n"
    "  | mcp_server_odoo.odoo_connection.OdooConnectionError: Authentication failed: "
    "Username/password authentication failed (Standard mode)\n"
    "  +------------------------------------\n"
    "\n"
    "During handling of the above exception, another exception occurred:\n"
    "\n"
    "Traceback (most recent call last):\n"
    "  File '/x/__main__.py', line 117, in main\n"
    "mcp_server_odoo.error_handling.MCPSystemError: Unexpected error: unhandled errors "
    "in a TaskGroup (1 sub-exception)\n"
    "Error: Unexpected error: unhandled errors in a TaskGroup (1 sub-exception)\n"
)
sys.exit(1)
"""

#: Dies silently. There is nothing to add, and the transport error must survive
#: unwrapped rather than being decorated with an empty reason.
_DIES_SILENTLY = """
import sys
sys.exit(1)
"""


def _server(tmp_path: Path, source: str) -> tuple[str, list[str]]:
    script = tmp_path / "server.py"
    script.write_text(source)
    return sys.executable, [str(script)]


async def test_the_reason_a_server_refused_to_start_reaches_the_caller(
    tmp_path: Path,
) -> None:
    command, args = _server(tmp_path, _REFUSES_TO_START)

    with pytest.raises(Exception) as caught:
        async with McpSession(command, args, timeout_s=5.0):
            pass

    message = str(caught.value)
    assert "MCP Server is disabled globally." in message, message
    # The frames above it are noise on a connection-test screen.
    assert "odoo_connection.py" not in message, message


async def test_a_server_that_dies_without_a_word_is_not_dressed_up(
    tmp_path: Path,
) -> None:
    """No invented reason. An empty stderr must leave the transport error alone,
    so "we could not tell you why" stays distinguishable from a real cause."""
    command, args = _server(tmp_path, _DIES_SILENTLY)

    with pytest.raises(Exception) as caught:
        async with McpSession(command, args, timeout_s=5.0):
            pass

    assert not isinstance(caught.value, McpServerStartupError), str(caught.value)


async def test_the_cause_wins_over_the_wrapper_that_hides_it(tmp_path: Path) -> None:
    """Python prints a chained traceback cause-before-effect, so the LAST line
    is the least informative one. This is the exact shape that reported
    "unhandled errors in a TaskGroup (1 sub-exception)" for what was really a
    rejected Odoo password."""
    command, args = _server(tmp_path, _WRAPS_THE_REAL_CAUSE)

    with pytest.raises(Exception) as caught:
        async with McpSession(command, args, timeout_s=5.0):
            pass

    message = str(caught.value)
    assert "Authentication failed" in message, message
    assert "TaskGroup" not in message, message
