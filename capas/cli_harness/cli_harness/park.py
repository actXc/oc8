"""Detects that a run was parked mid-turn by the MCP gateway -- either an
approval-gated tool call (mcp_gateway.py's REQUIRE_APPROVAL branch) or an
ask_user call (Task 1) -- so a container-driven plugin knows to stop polling
and tear its container down. Mirrors nanoclaw_runtime/runtime/runtime.py's own
isolated_result/_POLL_SECONDS check exactly, so an operator's decision is
never outlived by one poller and not the other."""

from __future__ import annotations

from oc8 import models as m

POLL_INTERVAL_S = 2.0

_PARK_STATUSES = frozenset({"waiting_for_approval", "waiting_for_input"})


def is_parked(run: m.AgentRun) -> tuple[bool, str]:
    result = run.context.get("isolated_result") or {}
    status = str(result.get("status", ""))
    if status in _PARK_STATUSES:
        return True, status
    return False, ""
