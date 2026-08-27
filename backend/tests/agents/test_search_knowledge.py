"""An agent in a container can reach its knowledge base.

The in-process engine retrieves knowledge once, at the start, from the task
text. A container-run agent cannot work that way: its task text is the trigger
("check the ticket inbox"), and what it needs to look up only becomes clear
once it has read the ticket. So the nanoclaw runtime retrieved nothing at all --
a knowledge base granted to such an agent was, in practice, unreachable.

Made a tool for the same reason skills are one: the agent asks when it knows
what to ask.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.control_tools import SEARCH_KNOWLEDGE, execute_control_tool
from oc8.authz.pdp import Decision, Effect
from oc8.modelrouter import ToolCall
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def _agent_and_task(db: Any, tenant: uuid.UUID) -> tuple[m.Agent, m.Task]:
    agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="Sina")
    db.add(agent)
    await db.flush()
    task = m.Task(
        tenant_id=tenant,
        department_id=agent.department_id,
        assigned_agent_id=agent.id,
        title="Ticket-Eingang",
        state="in_progress",
    )
    db.add(task)
    await db.flush()
    return agent, task


async def _run(db: Any, tenant: uuid.UUID, query: str, monkeypatch: Any, answer: str) -> Any:
    agent, task = await _agent_and_task(db, tenant)

    async def fake_retrieve(*a: Any, **kw: Any) -> tuple[str, bool]:
        fake_retrieve.seen = kw  # type: ignore[attr-defined]
        return answer, False

    monkeypatch.setattr("oc8.agent.control_tools.retrieve_kb_context", fake_retrieve)
    outcome = await execute_control_tool(
        db,
        tenant_id=tenant,
        agent=agent,
        task=task,
        tc=ToolCall(id="c1", name=SEARCH_KNOWLEDGE.name, arguments={"query": query}),
        decision=Decision(Effect.ALLOW),
        assigned_skills=[],
        active_skills=[],
        mcp_conn=None,
        originating_operator=None,
    )
    return outcome, fake_retrieve


async def test_the_agent_gets_what_the_knowledge_base_holds(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        outcome, retrieve = await _run(
            db, tenant, "Erstattungsfrist", monkeypatch, "Erstattungen: 14 Tage."
        )
    assert outcome is not None
    assert "14 Tage" in outcome.output
    # The agent's OWN question is what gets searched -- not the task text, which
    # for a scheduled agent says only "check the inbox".
    assert retrieve.seen["query_text"] == "Erstattungsfrist"


async def test_nothing_found_is_said_plainly(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty answer must read as "we have nothing on this", or the model
    fills the silence with something it invented."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        outcome, _r = await _run(db, tenant, "Raketenantrieb", monkeypatch, "")
    assert outcome is not None
    assert "nichts" in outcome.output.lower() or "no " in outcome.output.lower()


async def test_an_empty_query_is_refused(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        outcome, _r = await _run(db, tenant, "   ", monkeypatch, "irgendwas")
    assert outcome is not None
    assert outcome.output.startswith("ERROR")


async def test_the_model_locality_is_passed_so_restricted_material_stays_local(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrieval filters restricted content by where the model runs. Handing a
    cloud-model agent restricted text here would route it straight out through
    the LLM gateway, which cannot tell what it is carrying."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        _outcome, retrieve = await _run(db, tenant, "Preise", monkeypatch, "x")
    assert retrieve.seen["model_locality"] in ("cloud", "local")


async def test_a_lookup_leaves_a_trail(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Did the agent consult the handbook, or did it guess?" has to be
    answerable afterwards.

    It was not, and I got it wrong myself: with no audit event and no feed line,
    I counted zero lookups and reported that the agent had ignored the knowledge
    base -- while its answer quoted that knowledge base word for word. A tool
    whose use leaves no trace cannot be supervised, and for the one tool that
    decides whether a customer is told a fact or a guess, that is the wrong
    property to have.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _run(db, tenant, "Garantiedauer", monkeypatch, "5 Jahre auf die Konstruktion.")

        events = (await db.execute(m.ActivityEvent.__table__.select())).fetchall()
        assert any("Garantiedauer" in (e.message or "") for e in events), "no feed line"

        audit = (await db.execute(m.AuditEvent.__table__.select())).fetchall()
        assert any(a.action == "knowledge.searched" for a in audit), "no audit entry"
