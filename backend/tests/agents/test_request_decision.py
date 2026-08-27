"""An agent hands a decision to a human without stopping.

The goal is that people work in oc8 and not in the system behind it: when an
agent hits something it may not decide alone, the decision -- and everything
needed to make it -- has to arrive in the approvals inbox, not as a note inside
Odoo that someone has to go and find.

Two properties matter and they pull against each other:
  * the agent must NOT park -- a helpdesk agent with a queue behind it cannot
    hold a ticket (and a container) hostage to a human's lunch break;
  * the decision must still reach the agent, later, as work it performs.
So the tool records and returns, and the DECISION enqueues a fresh run.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.control_tools import REQUEST_DECISION, execute_control_tool
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


def _call(**arguments: Any) -> ToolCall:
    return ToolCall(id=str(uuid.uuid4()), name=REQUEST_DECISION.name, arguments=arguments)


ARGS: dict[str, Any] = {
    "question": "Sollen wir die doppelte Abbuchung erstatten?",
    "context": (
        "Ticket #42, Kunde Travis Mendoza. Rechnung 2026-0412 über 249,00 EUR "
        "wurde am 03. und am 05. abgebucht. Die zweite Buchung ist im System "
        "sichtbar. Der Kunde bittet um Erstattung."
    ),
    "options": [
        {"key": "full", "label": "Voll erstatten", "detail": "249,00 EUR zurückbuchen"},
        {"key": "half", "label": "50% Gutschrift", "detail": "Kulanz, Kunde bleibt belastet"},
        {"key": "decline", "label": "Ablehnen", "detail": "Buchung war korrekt"},
    ],
    "recommendation": "full",
}


async def _run_tool(db: Any, tenant: uuid.UUID, **overrides: Any) -> tuple[Any, m.Agent, m.Task]:
    agent, task = await _agent_and_task(db, tenant)
    outcome = await execute_control_tool(
        db,
        tenant_id=tenant,
        agent=agent,
        task=task,
        tc=_call(**{**ARGS, **overrides}),
        decision=Decision(Effect.ALLOW),
        assigned_skills=[],
        active_skills=[],
        mcp_conn=None,
        originating_operator=None,
    )
    return outcome, agent, task


async def test_the_decision_lands_in_the_inbox_with_its_basis(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        _outcome, agent, task = await _run_tool(db, tenant)

        approval = (
            await db.execute(m.ApprovalRequest.__table__.select())
        ).fetchone()
        assert approval is not None
        assert approval.action_type == "decision"
        assert approval.status == "pending"
        assert approval.agent_id == agent.id
        assert approval.task_id == task.id
        assert approval.title == ARGS["question"]
        # Everything the human needs to decide, without opening Odoo.
        assert "Rechnung 2026-0412" in approval.detail
        assert [o["key"] for o in approval.payload["options"]] == ["full", "half", "decline"]
        assert approval.payload["recommendation"] == "full"


async def test_the_agent_is_told_to_carry_on(app_session: AppSessionFactory) -> None:
    """It must not wait: `suspend` is what parks a run, and a parked run holds
    its ticket and its container until a human acts."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        outcome, _a, _t = await _run_tool(db, tenant)
        assert outcome is not None
        assert outcome.suspend is None
        assert "entscheidet" in outcome.output.lower() or "human" in outcome.output.lower()


async def test_a_decision_without_options_is_still_accepted(
    app_session: AppSessionFactory,
) -> None:
    """Not every question has neat alternatives. A plain "please decide" plus
    context must work, or the agent will paper over the gap with prose."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await _run_tool(db, tenant, options=[], recommendation=None)
        approval = (await db.execute(m.ApprovalRequest.__table__.select())).fetchone()
        assert approval is not None
        assert approval.payload["options"] == []


async def test_a_question_with_no_context_is_refused(
    app_session: AppSessionFactory,
) -> None:
    """An approval without its basis just moves the research onto the human --
    which is the thing this exists to stop."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        outcome, _a, _t = await _run_tool(db, tenant, context="")
        assert outcome is not None
        assert outcome.output.startswith("ERROR")
        assert (await db.execute(m.ApprovalRequest.__table__.select())).fetchone() is None
