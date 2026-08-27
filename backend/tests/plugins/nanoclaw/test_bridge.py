"""The bridge is the only reason a stdio-only harness can reach an HTTP PEP.

nanoclaw's McpServerConfig has no HTTP transport (command/args/env only), so the
gateway is reached through this shim -- which must add the Authorization header
the container's own config never sees.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

BRIDGE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "capas", "nanoclaw_runtime", "assets", "oc8-mcp-bridge.mjs",
)


class _Handler(BaseHTTPRequestHandler):
    seen: list[tuple[str, dict[str, object]]] = []

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append((self.headers.get("Authorization", ""), body))
        payload = json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": {"ok": True}})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload.encode())

    def log_message(self, *a: object) -> None:
        return


def test_a_request_is_forwarded_with_the_bearer_token() -> None:
    _Handler.seen = []
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/mcp"

    proc = subprocess.run(
        ["node", os.path.abspath(BRIDGE)],
        input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n",
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "OC8_MCP_URL": url, "OC8_TOKEN": "tok-123"},
    )
    server.shutdown()

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip())["result"] == {"ok": True}
    assert _Handler.seen[0][0] == "Bearer tok-123"
    assert _Handler.seen[0][1]["method"] == "tools/list"


def _run_bridge(stdin_lines: list[str]) -> subprocess.CompletedProcess[str]:
    _Handler.seen = []
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/mcp"
    try:
        return subprocess.run(
            ["node", os.path.abspath(BRIDGE)],
            input="\n".join(stdin_lines) + "\n",
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "OC8_MCP_URL": url, "OC8_TOKEN": "tok-123"},
        )
    finally:
        server.shutdown()


def test_a_bare_json_scalar_does_not_kill_the_process() -> None:
    # A line that parses as JSON but is not an object (here: the number 42)
    # must not crash the bridge -- `'id' in 42` throws a TypeError in plain
    # JS, and an uncaught exception here would exit the process and silently
    # disarm every tool for the rest of the run. The decisive assertion is
    # that the *next*, well-formed request still gets a reply: if the process
    # had died on line 1, this second reply would simply be missing.
    proc = _run_bridge([
        "42",
        json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/list"}),
    ])

    assert proc.returncode == 0, proc.stderr
    replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert len(replies) == 2, (replies, proc.stderr)
    assert replies[0]["id"] is None
    assert replies[0]["error"]["code"] == -32600
    assert replies[1] == {"jsonrpc": "2.0", "id": 9, "result": {"ok": True}}


def test_a_json_array_gets_an_error_reply_not_silence() -> None:
    # An array has no "id" key, so a shape check that only asks `'id' in x`
    # would misclassify it as a notification and drop it on the floor --
    # the caller then hangs forever with no reply and no error.
    proc = _run_bridge(["[1, 2, 3]"])

    assert proc.returncode == 0, proc.stderr
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, (lines, proc.stderr)
    reply = json.loads(lines[0])
    assert reply["error"]["code"] == -32600


def test_a_notification_gets_no_reply_line() -> None:
    proc = _run_bridge([json.dumps({"jsonrpc": "2.0", "method": "notify/thing"})])

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ""
