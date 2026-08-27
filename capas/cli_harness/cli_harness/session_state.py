"""Where a CLI-harness runtime plugin's own resume-correlation state lives in
run.context, shared so claude_code_runtime/codex_runtime/opencode_runtime
agree on the shape instead of drifting. Namespaced per plugin so two
different harness plugins never collide if an agent's runtime_ref is ever
reassigned mid-history (the old key is simply orphaned, not read by the new
plugin).

Both writers here go through ``oc8.runtime.run_context.merge_context``, and
that is NOT a stylistic choice -- it is what makes the ask_user/approval park
work at all. ``agent_run.context`` has two concurrent writers during a run:
this plugin's poll loop (from the executor's session) and ``mcp_gateway.py``
(from the HTTP request's own session, writing the ``isolated_result`` park
marker the poll loop is watching for). These functions used to build
``{**run.context, _KEY: ...}`` in Python and let the caller assign+commit it
-- a read-modify-write against a snapshot taken BEFORE the gateway's commit,
so the very next poll's session-id write erased the park marker it was
supposed to observe. The loop then never saw a parked run and spun until the
30-minute deadline; the run's ask_user never surfaced to the operator.
``merge_context`` does the merge inside ONE ``context || :patch`` statement so
Postgres' own row locking serialises the two writers instead -- see that
module's docstring, which documents this exact hazard for the approvals
endpoint that hit it first.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.runtime.run_context import merge_context

_KEY = "cli_harness_sessions"


def get_session_id(run: m.AgentRun, plugin_name: str) -> str | None:
    sessions = run.context.get(_KEY, {})
    value = sessions.get(plugin_name)
    return str(value) if value else None


async def set_session_id(
    db: AsyncSession, run: m.AgentRun, plugin_name: str, session_id: str
) -> dict[str, Any]:
    """Record this plugin's resume id, preserving every other context key.

    The patch names only `_KEY`, so `||`'s top-level merge leaves
    `isolated_result` (and anything else another writer has since committed)
    exactly as it found it. Nesting per-plugin ids one level down inside
    `_KEY` is safe under that shallow merge because exactly one plugin ever
    writes for a given run -- the run's agent has one runtime.
    """
    sessions = dict(run.context.get(_KEY, {}))
    sessions[plugin_name] = session_id
    return await merge_context(db, run, {_KEY: sessions})


async def clear_session_id(db: AsyncSession, run: m.AgentRun, plugin_name: str) -> dict[str, Any]:
    """Drop this plugin's resume id once the leg finished for good."""
    sessions = dict(run.context.get(_KEY, {}))
    sessions.pop(plugin_name, None)
    return await merge_context(db, run, {_KEY: sessions})
