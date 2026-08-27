"""Regression guard: the seeded demo MCP connection's `name` must match the
key its department frames grant permissions under.

`agent.engine._select_tool_connection` (or wherever the run is wired up) sets
`connection_key = mcp_conn.name` at tool-call time (agent/engine.py), and
`authz.pdp.authorize_tool_call` looks the tool policy up in the frame by that
same key -- design decision #3: the frame key IS the connection name. Every
department frame in `oc8.seed.DEPT_FRAMES` grants a `"demo-fs"` entry, and the
seeded connection's `scopes` were written to match it. But the seeded
`m.McpConnection` row itself was constructed with `name="Filesystem (demo)"`,
so at runtime `connection_key` was `"Filesystem (demo)"` -- which has no
frame entry -- and every tool call over the demo filesystem connection was
silently DENIED. That broke "Run now" against the demo connection, the
primary zero-setup, end-to-end demo path.

This test exercises the ACTUAL seeded rows (by running the real `_seed_acme`
seeder, monkeypatched onto a throwaway tenant id so it can't collide with the
shared `ACME_TENANT_ID` data other tests in the suite depend on) instead of a
fabricated `connection_key` -- which is exactly why the per-task unit tests
(e.g. `test_tool_frame_enforcement.py`) missed this: they all pass the frame
key in directly rather than deriving it from the seeded connection's real
`name`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.authz.pdp import Effect, authorize_tool_call, effective_tool_policies, required_right
from oc8.seed import _seed_acme
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_seeded_demo_connection_authorizes_read_and_write_via_its_own_name(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Run the real seeder onto a disposable tenant id (not ACME_TENANT_ID) so
    # this test can't collide with the shared-tenant data other suites rely on.
    fresh_tid = uuid.uuid4()
    monkeypatch.setattr("oc8.seed.ACME_TENANT_ID", fresh_tid)

    async with app_session(fresh_tid) as session:
        await _seed_acme(session)

        conn = (
            await session.execute(
                select(m.McpConnection).where(m.McpConnection.tenant_id == fresh_tid)
            )
        ).scalar_one()
        dept = (
            await session.execute(
                select(m.Department).where(
                    m.Department.tenant_id == fresh_tid,
                    m.Department.id == conn.department_id,
                )
            )
        ).scalar_one()

    # The department frame exactly as persisted by the seeder (DEPT_FRAMES ->
    # _frame_json -> Department.frame), and the connection exactly as
    # persisted (whatever `name`/`scopes` the seeder actually wrote). Mirrors
    # agent/engine.py's own `isinstance` narrowing of `mcp_conn.scopes`.
    policies = effective_tool_policies(dept.frame, {})
    tool_scopes = conn.scopes if isinstance(conn.scopes, dict) else None

    read_right = required_right("read_file", tool_scopes)
    write_right = required_right("write_file", tool_scopes)
    assert read_right == "read"
    assert write_right == "write"

    read_decision = authorize_tool_call(
        policies=policies, connection_key=conn.name, right=read_right, value=None
    )
    write_decision = authorize_tool_call(
        policies=policies, connection_key=conn.name, right=write_right, value=None
    )

    assert read_decision.effect is Effect.ALLOW, read_decision.reason
    assert write_decision.effect is Effect.ALLOW, write_decision.reason
