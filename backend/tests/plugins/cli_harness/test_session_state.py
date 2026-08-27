from __future__ import annotations

import uuid
from typing import Any

import pytest
from cli_harness.session_state import clear_session_id, get_session_id, set_session_id

from oc8 import models as m

pytestmark = pytest.mark.asyncio


async def _seed_run(app_session: Any, tenant: uuid.UUID, context: dict) -> uuid.UUID:
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Eng", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant,
            department_id=dept.id,
            name="Coder",
            status="running",
            narrowing={},
            definition={},
        )
        db.add(agent)
        await db.flush()
        run = m.AgentRun(tenant_id=tenant, agent_id=agent.id, state="running", context=context)
        db.add(run)
        await db.flush()
        return run.id


async def test_get_session_id_is_none_when_nothing_stored(app_session: Any) -> None:
    tenant = uuid.uuid4()
    run_id = await _seed_run(app_session, tenant, {})
    async with app_session(tenant) as db:
        run = await db.get(m.AgentRun, run_id)
        assert run is not None
        assert get_session_id(run, "claude_code_runtime") is None


async def test_set_then_get_round_trips(app_session: Any) -> None:
    tenant = uuid.uuid4()
    run_id = await _seed_run(app_session, tenant, {})
    async with app_session(tenant) as db:
        run = await db.get(m.AgentRun, run_id)
        assert run is not None
        await set_session_id(db, run, "claude_code_runtime", "sess-123")
        assert get_session_id(run, "claude_code_runtime") == "sess-123"


async def test_two_plugins_do_not_share_a_namespace(app_session: Any) -> None:
    tenant = uuid.uuid4()
    run_id = await _seed_run(app_session, tenant, {})
    async with app_session(tenant) as db:
        run = await db.get(m.AgentRun, run_id)
        assert run is not None
        await set_session_id(db, run, "claude_code_runtime", "claude-sess")
        await set_session_id(db, run, "codex_runtime", "codex-sess")
        assert get_session_id(run, "claude_code_runtime") == "claude-sess"
        assert get_session_id(run, "codex_runtime") == "codex-sess"


async def test_clear_removes_only_this_plugins_key(app_session: Any) -> None:
    tenant = uuid.uuid4()
    run_id = await _seed_run(app_session, tenant, {})
    async with app_session(tenant) as db:
        run = await db.get(m.AgentRun, run_id)
        assert run is not None
        await set_session_id(db, run, "claude_code_runtime", "claude-sess")
        await set_session_id(db, run, "codex_runtime", "codex-sess")
        await clear_session_id(db, run, "claude_code_runtime")
        assert get_session_id(run, "claude_code_runtime") is None
        assert get_session_id(run, "codex_runtime") == "codex-sess"


async def test_existing_context_keys_survive_a_set(app_session: Any) -> None:
    tenant = uuid.uuid4()
    run_id = await _seed_run(app_session, tenant, {"task": "do the thing"})
    async with app_session(tenant) as db:
        run = await db.get(m.AgentRun, run_id)
        assert run is not None
        await set_session_id(db, run, "claude_code_runtime", "sess-123")
        assert run.context["task"] == "do the thing"


async def test_a_concurrent_writers_key_is_not_erased(app_session: Any) -> None:
    """The regression that made ask_user hang for the full 30-minute deadline.

    Two sessions write `agent_run.context` during a run: the plugin's poll
    loop (this `db`) and mcp_gateway.py's ask_user/approval branch, which
    writes the `isolated_result` park marker the poll loop is watching for.
    These functions used to merge in PYTHON off a snapshot loaded before the
    gateway committed, so the next session-id write erased the marker and the
    loop never saw its own run park. Simulated here by committing the marker
    from a SECOND session while the first still holds its stale copy."""
    tenant = uuid.uuid4()
    run_id = await _seed_run(app_session, tenant, {})

    async with app_session(tenant) as db:
        run = await db.get(m.AgentRun, run_id)
        assert run is not None
        # The plugin's snapshot predates the gateway's write, exactly as it
        # does in production (the run row is loaded at execute()'s start).
        assert "isolated_result" not in run.context

        async with app_session(tenant) as gateway_db:
            gateway_run = await gateway_db.get(m.AgentRun, run_id)
            assert gateway_run is not None
            gateway_run.context = {
                **gateway_run.context,
                "isolated_result": {"status": "waiting_for_input", "output": "Which invoice?"},
            }

        await set_session_id(db, run, "claude_code_runtime", "sess-123")

    async with app_session(tenant) as db:
        run = await db.get(m.AgentRun, run_id)
        assert run is not None
        assert run.context["cli_harness_sessions"]["claude_code_runtime"] == "sess-123"
        # The whole point: the gateway's key is still there.
        assert run.context["isolated_result"]["status"] == "waiting_for_input"
