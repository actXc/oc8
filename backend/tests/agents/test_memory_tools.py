"""The memory tiers, reachable from a container.

The in-process engine injects memory into the preamble and parks a run for a
company-tier write. A container-run agent got neither: the runtime injects no
memory, and the tool gateway refuses every core tool -- so an agent there could
not read what it had learned, nor write anything down. The whole second stage of
knowledge management was, for the runtime that actually runs, absent.

Same shape as search_knowledge: reading is a tool, because what to recall only
becomes clear once the agent has read the ticket.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.control_tools import MEMORY_WRITE, SEARCH_MEMORY, execute_control_tool
from oc8.authz.pdp import Decision, Effect
from oc8.memory.policy import authorize_memory_write
from oc8.modelrouter import ToolCall
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

FRAME = {"memory": {"department": ["read", "write"], "company": ["read"]}}


async def _setup(db: Any, tenant: uuid.UUID) -> tuple[m.Agent, m.Task]:
    dept = m.Department(tenant_id=tenant, name="Kundenservice", frame=FRAME)
    db.add(dept)
    await db.flush()
    agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Sina", narrowing={})
    db.add(agent)
    await db.flush()
    task = m.Task(
        tenant_id=tenant, department_id=dept.id, assigned_agent_id=agent.id,
        title="Ticket", state="in_progress",
    )
    db.add(task)
    await db.flush()
    return agent, task


async def _call(db: Any, tenant: uuid.UUID, agent: m.Agent, task: m.Task, name: str,
                args: dict[str, Any], decision: Decision) -> Any:
    return await execute_control_tool(
        db, tenant_id=tenant, agent=agent, task=task,
        tc=ToolCall(id=str(uuid.uuid4()), name=name, arguments=args),
        decision=decision, assigned_skills=[], active_skills=[],
        mcp_conn=None, originating_operator=None,
    )


async def test_what_was_written_can_be_recalled_later(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the tier: something learned on one ticket is available on the
    next, in a different run, with no shared conversation between them."""
    tenant = uuid.uuid4()

    async def fake_recall(*a: Any, **kw: Any) -> str:
        fake_recall.seen = kw  # type: ignore[attr-defined]
        return "[Memory: department]\n- Kunde Meier hat Sonderkonditionen."

    monkeypatch.setattr("oc8.agent.control_tools.retrieve_context", fake_recall)
    async with app_session(tenant) as db:
        agent, task = await _setup(db, tenant)
        wrote = await _call(
            db, tenant, agent, task, MEMORY_WRITE.name,
            {"tier": "department", "content": "Kunde Meier hat Sonderkonditionen."},
            authorize_memory_write(FRAME, {}, "department"),
        )
        assert wrote is not None and not wrote.output.startswith("ERROR")

        recalled = await _call(
            db, tenant, agent, task, SEARCH_MEMORY.name,
            {"query": "Sonderkonditionen Meier"}, Decision(Effect.ALLOW),
        )
        assert recalled is not None
        assert "Sonderkonditionen" in recalled.output
        # The agent's OWN question is the query -- the task text says only
        # "check the inbox".
        assert fake_recall.seen["query_text"] == "Sonderkonditionen Meier"


async def test_a_company_write_is_recorded_but_does_not_stop_the_run(
    app_session: AppSessionFactory,
) -> None:
    """Company memory always needs a human (§10.1), and no frame can waive that.
    In a container the run must NOT park for it: the record is already stored as
    pending and the approval flips its status later, so waiting buys nothing and
    costs the queue behind this agent.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _setup(db, tenant)
        outcome = await _call(
            db, tenant, agent, task, MEMORY_WRITE.name,
            {"tier": "company", "content": "Ab 1.9. gilt die neue Preisliste."},
            authorize_memory_write(FRAME, {}, "company"),
        )
        assert outcome is not None
        assert outcome.suspend is None, "a container run must not park for this"

        record = (await db.execute(m.MemoryRecord.__table__.select())).fetchone()
        assert record is not None and record.status == "pending"

        approval = (await db.execute(m.ApprovalRequest.__table__.select())).fetchone()
        assert approval is not None
        assert approval.action_type == "memory_write"
        assert approval.payload["memory_record_id"] == str(record.id)
        # The agent has to know it is not in force yet, or it will act on it.
        assert "freigegeben" in outcome.output.lower() or "approv" in outcome.output.lower()


async def test_an_agents_own_notebook_needs_no_grant(
    app_session: AppSessionFactory,
) -> None:
    """The agent tier is always readable and writable -- it is the agent's own
    notebook, and a frame grants access to what is SHARED. Asserted because it is
    surprising next to the other two tiers, and because a future tightening here
    would break every agent silently."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _setup(db, tenant)
        outcome = await _call(
            db, tenant, agent, task, MEMORY_WRITE.name,
            {"tier": "agent", "content": "Kunde ruft immer freitags an."},
            authorize_memory_write(FRAME, {}, "agent"),
        )
        assert outcome is not None
        assert not outcome.output.startswith("ERROR")


async def test_an_unknown_tier_is_refused(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _setup(db, tenant)
        outcome = await _call(
            db, tenant, agent, task, MEMORY_WRITE.name,
            {"tier": "abteilungsleitung", "content": "x"},
            authorize_memory_write(FRAME, {}, "abteilungsleitung"),
        )
        assert outcome is not None
        assert outcome.output.startswith("ERROR")


async def test_recalling_nothing_is_said_plainly(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "oc8.agent.control_tools.retrieve_context",
        lambda *a, **kw: _empty(),
    )
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _setup(db, tenant)
        outcome = await _call(
            db, tenant, agent, task, SEARCH_MEMORY.name, {"query": "x"}, Decision(Effect.ALLOW)
        )
        assert outcome is not None
        assert "nichts" in outcome.output.lower()


async def _empty() -> str:
    return ""
