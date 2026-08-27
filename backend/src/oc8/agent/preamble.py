"""What every runtime must put in front of the model before the first step.

Extracted from the in-process engine so the isolated runtime seeds the SAME
context. It used to seed only the system prompt, which made an agent under
isolation strictly weaker than the same agent in-process: it could not name a
colleague to delegate to (no roster), could not invoke a skill it was never told
about (no catalog), and wrote memories nothing ever read back (no memory
context). Anything added here reaches both runtimes at once -- that is the whole
point of the module.

Core-neutral: names no vendor, product or software specifics.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.agent.provenance import RULE as PROVENANCE_RULE
from oc8.knowledge.retrieval import retrieve_kb_context
from oc8.memory.router import retrieve_context
from oc8.modelrouter import NeutralMessage
from oc8.skills.runtime import LoadedSkill, catalog_block, load_assigned_skills


def system_prompt(agent: m.Agent) -> str:
    pres = agent.presentation or {}
    guardrails = pres.get("guardrails", [])
    parts = [f"You are {agent.name}" + (f", {agent.role_title}." if agent.role_title else ".")]
    if agent.mission:
        parts.append(agent.mission)
    if guardrails:
        parts.append("Guardrails you must respect:\n" + "\n".join(f"- {g}" for g in guardrails))
    parts.append(
        "You have tools available. To use a tool you MUST invoke it through the "
        "function-calling interface — never write the tool call as text or JSON in "
        "your reply. Call one tool at a time and wait for its result. When the task "
        "is fully done, reply with a short plain-text summary and call no further tools."
    )
    return "\n\n".join(parts)


@dataclass
class RunPreamble:
    """The seeded conversation plus what the caller needs downstream.

    ``skill_tool_names`` and ``contains_restricted`` are carried here rather than
    recomputed by each caller: the first decides which tool names bypass the
    frame check (getting it wrong is a security hole), the second decides whether
    a checkpoint may leave the tenant's locality. Deriving either twice is how
    the two runtimes drift apart.
    """

    messages: list[NeutralMessage]
    assigned_skills: list[LoadedSkill] = field(default_factory=list)
    skill_tool_names: frozenset[str] = frozenset()
    contains_restricted: bool = False


async def roster_block(db: AsyncSession, *, agent: m.Agent) -> str | None:
    """The agent's department colleagues, or None if it has none.

    A team lead can only name a real agent_id if it knows its colleagues, so
    without this delegate_task is offered but unusable -- every call denied as an
    invalid agent_id.

    Public because the container runtime has to build the same instructions from
    the outside. It was private, and the runtime plugin composed its standing
    file from system_prompt + catalog + delivery only -- so a lead in a container
    was offered delegate_task and could not name one colleague, while the same
    lead in-process could. Observed live: an accepted handoff told Sina to
    delegate, and her instructions never mentioned that Jan exists.
    """
    mates = (
        (
            await db.execute(
                select(m.Agent).where(
                    m.Agent.department_id == agent.department_id,
                    m.Agent.id != agent.id,
                    m.Agent.status != "pending_approval",
                    m.Agent.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not mates:
        return None
    roster = "\n".join(
        f"- {a.id}: {a.name}" + (f" ({a.role_title})" if a.role_title else "") for a in mates
    )
    return f"Your department's agents:\n{roster}"


async def build_run_preamble(
    db: AsyncSession,
    *,
    agent: m.Agent,
    tenant_id: uuid.UUID,
    task_text: str,
    frame: dict[str, Any],
    model_locality: str,
) -> RunPreamble:
    """Seed a run's conversation: system context first, the task last.

    Message order is part of the contract -- the task must be the final turn, so
    the model reads its instructions against context already established.
    """
    messages: list[NeutralMessage] = [
        NeutralMessage(role="system", content=system_prompt(agent))
    ]
    # Said once, before anything a stranger wrote can arrive. Every answer a
    # connection returns is fenced as <external>, and this is what makes that
    # fence mean something to the model. It is the cheap half of the defence --
    # the half that holds without the model's cooperation is the blast-radius
    # limit in agent/blast_radius.py.
    messages.append(NeutralMessage(role="system", content=PROVENANCE_RULE))

    memory_ctx = await retrieve_context(
        db, agent=agent, tenant_id=tenant_id, frame=frame, query_text=task_text
    )
    if memory_ctx:
        messages.append(NeutralMessage(role="system", content=memory_ctx))

    kb_ctx, contains_restricted = await retrieve_kb_context(
        db,
        agent=agent,
        tenant_id=tenant_id,
        query_text=task_text,
        frame=frame,
        model_locality=model_locality,
    )
    if kb_ctx:
        messages.append(NeutralMessage(role="system", content=kb_ctx))

    if agent.is_team_lead:
        # Appended as its own system message (like memory/KB context) because
        # system_prompt is a pure sync function and this needs the DB.
        roster = await roster_block(db, agent=agent)
        if roster is not None:
            messages.append(NeutralMessage(role="system", content=roster))

    assigned_skills = await load_assigned_skills(db, agent=agent, tenant_id=tenant_id)
    catalog = catalog_block(assigned_skills)
    if catalog:
        messages.append(NeutralMessage(role="system", content=catalog))

    messages.append(NeutralMessage(role="user", content=task_text))

    return RunPreamble(
        messages=messages,
        assigned_skills=list(assigned_skills),
        # Only these names bypass the frame check in _authorize -- computed once
        # so a connection tool that merely happens to be named `skill_*` (an MCP
        # server can name anything) is never mistaken for an assigned skill.
        skill_tool_names=frozenset(s.tool_name for s in assigned_skills),
        contains_restricted=contains_restricted,
    )
