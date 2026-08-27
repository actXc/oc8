"""The tools the CORE owns, as opposed to a connection's MCP tools.

Remember something, ask the operator, delegate to a colleague, invoke a skill --
capabilities the platform itself provides, so they must exist for every runtime.
They used to be defined and dispatched inline in engine.py's run loop, which is
why the isolated runtime offered none of them: there was no seam to share, only
a closure.

Core-neutral by construction: these name no vendor, product or software. A
connection's tools stay entirely the connection's business.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.agent.components import COMPONENT_CATALOG
from oc8.approvals import raise_approval
from oc8.audit import append_event
from oc8.authz.pdp import Decision, Effect
from oc8.knowledge.retrieval import retrieve_kb_context
from oc8.memory.router import retrieve_context, write_memory
from oc8.modelrouter import NeutralTool, ToolCall
from oc8.realtime.emit import record_activity
from oc8.runtime.repository import RunRepository
from oc8.skills.runtime import LoadedSkill, instruction_block, skill_tool_schemas

MEMORY_WRITE = NeutralTool(
    name="memory_write",
    description=(
        "Write a note to your memory. tier='agent' is private to you; "
        "'department' is shared with your department's other agents; "
        "'company' is shared tenant-wide but requires human approval before "
        "it becomes visible to anyone."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tier": {"type": "string", "enum": ["agent", "department", "company"]},
            "content": {"type": "string", "description": "The fact or note to remember."},
        },
        "required": ["tier", "content"],
    },
)

ASK_USER = NeutralTool(
    name="ask_user",
    description=(
        "Ask the human operator a question and pause until they answer. "
        "Use this when you are missing information you cannot obtain yourself. "
        "IMPORTANT: ask BEFORE taking any action that changes external state "
        "(sending, writing, paying) -- on resume the task re-runs from the "
        "start, so anything you did before asking would happen again."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question for the human."},
        },
        "required": ["question"],
    },
)

DELEGATE_TASK = NeutralTool(
    name="delegate_task",
    description=(
        "Delegate a piece of work to another agent in your department. Use "
        "this to break a large task into focused sub-tasks. The agent runs "
        "independently and you will be notified when it finishes."
    ),
    parameters={
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "The target agent's id, from your department roster.",
            },
            "task_text": {
                "type": "string",
                "description": "The instruction for the sub-task.",
            },
        },
        "required": ["agent_id", "task_text"],
    },
)


REQUEST_DECISION = NeutralTool(
    name="request_decision",
    description=(
        "Hand a decision you may not make alone to a human, WITHOUT waiting. "
        "Use this whenever the next step needs a person -- a refund, a credit, a "
        "contract change, a complaint, anything with legal or financial weight. "
        "You keep working and move on; the human decides in their inbox and you "
        "are given the decision later as a new task. Put EVERYTHING they need in "
        "`context`: they must be able to decide without opening the source "
        "system. Offer concrete `options` when there are real alternatives."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The decision to be made, in one sentence.",
            },
            "context": {
                "type": "string",
                "description": (
                    "The full basis for the decision: which record, which "
                    "customer, what you found, what is at stake."
                ),
            },
            "options": {
                "type": "array",
                "description": "Concrete alternatives you propose. May be empty.",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "label": {"type": "string"},
                        "detail": {"type": "string"},
                    },
                    "required": ["key", "label"],
                },
            },
            "recommendation": {
                "type": "string",
                "description": "The key of the option you would choose, if any.",
            },
        },
        "required": ["question", "context"],
    },
)


SEARCH_KNOWLEDGE = NeutralTool(
    name="search_knowledge",
    description=(
        "Look something up in your department's knowledge base -- policies, "
        "procedures, product facts, anything written down for you. Ask BEFORE "
        "telling a customer something you are not certain of: what comes back is "
        "what your organisation actually says, and inventing an answer instead is "
        "how a wrong promise reaches a customer. Ask in your own words, as "
        "specifically as you can."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What you want to know, phrased as a question or topic.",
            },
        },
        "required": ["query"],
    },
)


SEARCH_MEMORY = NeutralTool(
    name="search_memory",
    description=(
        "Recall what you or your department have written down before -- a "
        "customer's arrangement, a known problem and its fix, a decision that "
        "was made. This is memory from EARLIER runs, not from this conversation. "
        "Ask before you answer something that may already have been handled, so "
        "a customer is not asked the same question twice."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What you are trying to remember.",
            },
        },
        "required": ["query"],
    },
)

RENDER_COMPONENT = NeutralTool(
    name="render_component",
    description=(
        "Show the human a structured card instead of describing it in prose. "
        "Use this for one concrete record you already looked up (a deal, a "
        "ticket, an order) -- a title, a few key facts as label/value pairs, "
        "and an optional link back to the source system. Only use data you "
        "already obtained through a real tool call in this conversation; "
        "never invent field values. `component_key` must be one you have "
        "been granted -- if you are unsure, try 'record_card'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "component_key": {
                "type": "string",
                "description": "Which card layout to render, e.g. 'record_card'.",
            },
            "props": {
                "type": "object",
                "description": "The layout's own fields (see its description).",
            },
        },
        "required": ["component_key", "props"],
    },
)

CONTROL_TOOL_SCHEMAS: dict[str, NeutralTool] = {
    MEMORY_WRITE.name: MEMORY_WRITE,
    ASK_USER.name: ASK_USER,
    DELEGATE_TASK.name: DELEGATE_TASK,
    REQUEST_DECISION.name: REQUEST_DECISION,
    SEARCH_KNOWLEDGE.name: SEARCH_KNOWLEDGE,
    SEARCH_MEMORY.name: SEARCH_MEMORY,
    RENDER_COMPONENT.name: RENDER_COMPONENT,
}
CONTROL_TOOL_NAMES: frozenset[str] = frozenset(CONTROL_TOOL_SCHEMAS)

# How many delegation hops one chain may take before delegate_task is denied
# (§7). A wake-up carries its sub-task's depth unchanged -- it's a continuation,
# not a new hop -- so this counts real delegations, not round trips.
MAX_DELEGATION_DEPTH = 5
# Single source of truth for the depth-limit DENY reason, so _authorize (which
# raises it) and the dispatch below (which emits the operator ActivityEvent only
# for it) agree exactly -- never re-derive the depth arithmetic in two places, or
# an unrelated DENY on a task already at the cap mislabels the audit.
DEPTH_LIMIT_REASON = f"delegation depth limit reached (max {MAX_DELEGATION_DEPTH})"


def offered_tools(
    agent: m.Agent,
    *,
    assigned_skills: Sequence[LoadedSkill],
    active_skills: Sequence[LoadedSkill],
    mcp_tools: Sequence[NeutralTool],
    has_knowledge: bool = False,
) -> list[NeutralTool]:
    """The full tool list to offer the model this step.

    delegate_task is withheld from a non-lead deliberately: _authorize denies it
    for them on every call, so offering it would only invite calls that can never
    succeed. search_knowledge is withheld the same way when the agent has no
    knowledge base granted at all (has_knowledge, from the preamble's
    granted_kb_ids check): execute_control_tool already degrades a call to it
    gracefully, but a tool that can only ever answer "nothing in the knowledge
    base" is noise in the model's tool list, not a capability.
    """
    # Skill tools stay offered even once active: a model that invokes an
    # already-active skill again just hits the no-op branch in
    # execute_control_tool. Withdrawing the tool the moment it activates would
    # strand a model that re-checks its own tool list mid-task with an unknown
    # tool name instead of a harmless "already active" response.
    offered = [MEMORY_WRITE, ASK_USER, RENDER_COMPONENT]
    if agent.is_team_lead:
        offered.append(DELEGATE_TASK)
    if has_knowledge:
        offered.append(SEARCH_KNOWLEDGE)
    offered.extend(skill_tool_schemas(assigned_skills))

    if active_skills:
        wanted = {r.tool for s in active_skills for r in s.definition.requires_tools}
        # An active skill focuses the model on its own tools. This changes what is
        # OFFERED only -- _authorize still checks the frame on every call, so this
        # can never widen anything. The fallback matters: a skill whose required
        # tools this connection does not have must not leave the model with no
        # connection tools at all, or it cannot act.
        narrowed = [t for t in mcp_tools if t.name in wanted]
        offered.extend(narrowed or mcp_tools)
    else:
        offered.extend(mcp_tools)
    return offered


# ------------------------------------------------------------------ execution


@dataclass
class ControlOutcome:
    """What a control tool did, for the caller to apply.

    Deliberately data, not side effects on the caller's state: the two runtimes
    keep skill activation and pending sub-runs in different places (an in-memory
    list vs. the run's context row), so the dispatcher reports and the caller
    stores. That is what lets one implementation serve a loop and an HTTP API.
    """

    output: str
    suspend: str | None = None
    pending_run: uuid.UUID | None = None
    activated_skill: LoadedSkill | None = None
    #: Set only by render_component: {"component_key": str, "props": dict}
    #: for the CALLER to publish as a "run.component_rendered" realtime event.
    #: Data, not a side effect performed here, for the same reason
    #: pending_run is reported rather than published from inside this
    #: function -- the caller is the one holding the run id.
    rendered_component: dict[str, Any] | None = None


async def _delegate(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent: m.Agent,
    task: m.Task,
    tc: ToolCall,
    mcp_conn: m.McpConnection | None,
) -> tuple[str, uuid.UUID | None]:
    """Create the target agent's queued run for a delegated sub-task (§7).

    Returns (tool output for the model, new run id or None on rejection). It
    neither commits nor publishes: the in-process caller's transaction carries a
    transaction-local RLS tenant binding, so a commit here would unbind the
    tenant for the rest of the run. The caller reports the id onward and the
    executor publishes it after committing.

    _authorize has already checked that agent_id parses, isn't self, and is
    within the depth limit; the DB-dependent checks live here.
    """
    target = await db.get(m.Agent, uuid.UUID(str(tc.arguments["agent_id"])))
    if target is None or target.deleted_at is not None:
        return "ERROR: no such agent in your department", None
    if target.department_id != agent.department_id:
        return "ERROR: you can only delegate to agents in your own department", None

    context: dict[str, Any] = {
        "task": str(tc.arguments["task_text"]),
        "parent_task_id": str(task.id),
        "delegation_depth": task.delegation_depth + 1,
    }
    # Deferred import: oc8.runtime.executor reaches oc8.runtime.adapter, which
    # imports this module's own importer (oc8.agent.engine) at module level, so
    # importing it at the top would be a cycle. Resolved once, at first call.
    from oc8.runtime.executor import agent_has_own_login_binding

    # A Credential-backed LOGIN is never inherited. There IS a per-agent MCP
    # binding now (agent tool login selection design): passing the delegating
    # run's login id here would land in the sub-agent's context, where it
    # outranks that agent's OWN pin and suppresses the missing-pin error -- the
    # sub-agent would silently act in the external system as the delegating lead.
    #
    # A department-scoped connection is shared by construction, so inheriting it
    # normally reaches the same system the sub-agent would have found for itself
    # -- otherwise it would have no tools. The exception is a sub-agent whose own
    # narrowing already claims one of its tool keys, pinned or naming a login it
    # never pinned: an inherited id is read FIRST at resolution and returned, so
    # propagating one would silently borrow the department's connection in place
    # of the loud missing-pin error that agent is owed.
    #
    # Either way, propagating nothing sends the sub-agent down the normal
    # resolution path, which finds its own login or fails loudly.
    if (
        mcp_conn is not None
        and mcp_conn.credential_id is None
        and not await agent_has_own_login_binding(db, target)
    ):
        context["mcp_connection_id"] = str(mcp_conn.id)
    sub_run = await RunRepository(db).create(
        tenant_id=tenant_id, agent_id=target.id, context=context, source="delegation"
    )
    return f"delegated to {target.name} (run {sub_run.id})", sub_run.id


async def _department_frame(db: AsyncSession, agent: m.Agent) -> dict[str, Any]:
    """The department's frame, which decides which classifications this agent
    may retrieve at all. Empty means the strictest default, not "anything"."""
    dept = await db.get(m.Department, agent.department_id)
    return dict(dept.frame or {}) if dept is not None else {}


async def _model_locality(db: AsyncSession, agent: m.Agent) -> str:
    """Where this agent's model runs. "cloud" when unknown -- the stricter of
    the two, since it is what excludes restricted material from retrieval."""
    if agent.model_config_id is None:
        return "cloud"
    config = await db.get(m.ModelConfig, agent.model_config_id)
    return str(getattr(config, "locality", "cloud") or "cloud")


async def _has_component_grant(
    db: AsyncSession, *, agent: m.Agent, component_key: str
) -> bool:
    """Whether AGENT -- directly, or via its department -- has been granted
    this component. Mirrors oc8.knowledge.retrieval.granted_kb_ids: the same
    department/agent grant shape, keyed on a fixed catalogue string instead
    of a tenant-created knowledge base id."""
    result = await db.execute(
        select(m.ComponentGrant.id)
        .where(
            m.ComponentGrant.component_key == component_key,
            m.ComponentGrant.grantee_id.in_([agent.id, agent.department_id]),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def execute_control_tool(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent: m.Agent,
    task: m.Task,
    tc: ToolCall,
    decision: Decision,
    assigned_skills: Sequence[LoadedSkill],
    active_skills: Sequence[LoadedSkill],
    mcp_conn: m.McpConnection | None,
    originating_operator: str | None,
) -> ControlOutcome | None:
    """Run one core-owned tool call, or return None if it isn't one.

    None means "not mine" -- the caller must route the call to the connection's
    MCP server. Returning an error string instead would silently swallow every
    connection tool.
    """
    skill_by_tool = {s.tool_name: s for s in assigned_skills}

    if tc.name == SEARCH_MEMORY.name:
        query = str(tc.arguments.get("query", "")).strip()
        if not query:
            return ControlOutcome(output="ERROR: search_memory requires a query")
        # `retrieve_context` walks the tiers the frame grants READ on, so the
        # policy travels with the call rather than being re-derived here.
        recalled = await retrieve_context(
            db,
            agent=agent,
            tenant_id=tenant_id,
            frame=await _department_frame(db, agent),
            query_text=query,
        )
        if not recalled.strip():
            return ControlOutcome(
                output=(
                    "Dazu ist nichts notiert. Behandle den Fall als neu -- erfinde "
                    "keine Vorgeschichte."
                )
            )
        await record_activity(
            db,
            tenant_id=tenant_id,
            agent_id=agent.id,
            status="info",
            message=f"Erinnert sich an: {query}",
            detail=recalled[:500],
        )
        return ControlOutcome(output=recalled)

    if tc.name == SEARCH_KNOWLEDGE.name:
        query = str(tc.arguments.get("query", "")).strip()
        if not query:
            return ControlOutcome(output="ERROR: search_knowledge requires a query")
        # The AGENT's question, not the task text. For a scheduled agent the task
        # says only "check the inbox"; what it needs to look up becomes clear
        # only once it has read the ticket, which is the whole reason this is a
        # tool rather than something retrieved once at the start.
        #
        # `retrieve_kb_context` carries the access rules with it: only knowledge
        # bases granted to this agent, and restricted material only when the
        # model runs locally. Passing the real locality matters -- text handed
        # back here goes on to the model through the LLM gateway, which cannot
        # tell what it is carrying.
        context, _restricted = await retrieve_kb_context(
            db,
            agent=agent,
            tenant_id=tenant_id,
            query_text=query,
            frame=await _department_frame(db, agent),
            model_locality=await _model_locality(db, agent),
        )
        # A trail, because "did it consult the handbook or guess?" has to be
        # answerable afterwards. Without it I drew the wrong conclusion myself:
        # counted zero lookups and reported the knowledge base ignored, while the
        # answer quoted it word for word.
        await append_event(
            db,
            tenant_id=tenant_id,
            actor_type="agent",
            actor_id=agent.id,
            category="tool_action",
            action="knowledge.searched",
            resource={
                "agent_id": str(agent.id),
                "task_id": str(task.id),
                "query": query,
                "found": bool(context.strip()),
            },
            originating_operator=originating_operator,
        )
        await record_activity(
            db,
            tenant_id=tenant_id,
            agent_id=agent.id,
            status="info",
            message=f"Schlägt nach: {query}",
            detail=(context[:500] or None),
        )
        if not context.strip():
            # Said plainly, because a silent empty answer is filled in by the
            # model with something it made up.
            return ControlOutcome(
                output=(
                    "Dazu steht nichts in der Wissensdatenbank. Sage dem Kunden "
                    "nichts, was du dir selbst zusammenreimst -- frage nach oder "
                    "gib an einen Menschen ab."
                )
            )
        return ControlOutcome(output=context)

    if tc.name == REQUEST_DECISION.name:
        question = str(tc.arguments.get("question", "")).strip()
        context = str(tc.arguments.get("context", "")).strip()
        if not question:
            return ControlOutcome(output="ERROR: request_decision requires a question")
        if not context:
            # Refused rather than accepted thin: an approval without its basis
            # moves the research onto the human, which is the thing this exists
            # to stop.
            return ControlOutcome(
                output=(
                    "ERROR: request_decision requires `context` -- everything the "
                    "human needs to decide without opening the source system"
                )
            )
        raw_options = tc.arguments.get("options") or []
        options = [
            {
                "key": str(o.get("key", "")),
                "label": str(o.get("label", "")),
                "detail": str(o.get("detail", "")),
            }
            for o in raw_options
            if isinstance(o, dict) and o.get("key") and o.get("label")
        ]
        recommendation = tc.arguments.get("recommendation")
        approval = await raise_approval(
            db,
            tenant_id=tenant_id,
            agent_id=agent.id,
            task_id=task.id,
            action_type="decision",
            title=question,
            detail=context,
            payload={
                "options": options,
                "recommendation": str(recommendation) if recommendation else None,
                "task_title": task.title,
            },
        )
        await append_event(
            db,
            tenant_id=tenant_id,
            actor_type="agent",
            actor_id=agent.id,
            category="human_loop",
            action="decision.requested",
            resource={
                "approval_id": str(approval.id),
                "agent_id": str(agent.id),
                "task_id": str(task.id),
            },
            originating_operator=originating_operator,
        )
        await record_activity(
            db,
            tenant_id=tenant_id,
            agent_id=agent.id,
            status="warning",
            message=f"Entscheidung angefragt: {question}",
            detail=context[:500],
        )
        from oc8.realtime.bus import get_event_bus

        await get_event_bus().publish_event(
            tenant_id,
            "approval.created",
            {
                "approval_id": str(approval.id),
                "agent_id": str(agent.id),
                # Carried in the envelope on purpose: the executor commits, not
                # us, so a fresh session reading this row would see nothing
                # (see EventBus._push_payload).
                "title": approval.title,
                "detail": approval.detail,
            },
            source=f"oc8/approval/{approval.id}",
        )
        # Deliberately NOT a suspend: a queue behind this agent must not wait on
        # a human's lunch break. The decision comes back later as its own run.
        return ControlOutcome(
            output=(
                "Recorded. A human decides this in their approvals inbox; you will "
                "be given the decision later as a new task. Carry on with the rest "
                "of your work now, and tell the customer only that a colleague is "
                "reviewing it -- promise nothing."
            )
        )

    if tc.name == ASK_USER.name:
        question = str(tc.arguments.get("question", "")).strip()
        if not question:
            # An empty question is a model error, not a suspend: parking the run
            # would leave a human staring at nothing to answer.
            return ControlOutcome(output="ERROR: ask_user requires a non-empty question")
        return ControlOutcome(output=question, suspend="waiting_for_input")

    if tc.name == MEMORY_WRITE.name:
        if decision.effect is Effect.DENY:
            return ControlOutcome(output=f"ERROR: {decision.reason or 'memory write denied'}")
        if decision.effect is Effect.REQUIRE_APPROVAL:
            # Company memory always needs a human (§10.1) and no frame waives it.
            # The record is stored PENDING either way and the approval only flips
            # its status, so a container run gains nothing by waiting -- and the
            # queue behind this agent loses. The in-process engine parks here
            # because plain text IS its answer; this runtime does not have to.
            record = await write_memory(
                db,
                tenant_id=tenant_id,
                agent=agent,
                tier=str(tc.arguments.get("tier", "")),
                content=str(tc.arguments.get("content", "")),
                metadata={"task_id": str(task.id)},
            )
            await raise_approval(
                db,
                tenant_id=tenant_id,
                agent_id=agent.id,
                task_id=task.id,
                action_type="memory_write",
                title=f"{agent.name} wants to write company memory",
                detail=decision.reason or "",
                payload={
                    "memory_record_id": str(record.id),
                    "tier": str(tc.arguments.get("tier", "")),
                    "content": str(tc.arguments.get("content", "")),
                },
            )
            return ControlOutcome(
                output=(
                    "Notiert, aber noch NICHT freigegeben: Firmenwissen muss ein "
                    "Mensch bestaetigen. Handle vorerst nicht danach und nenne es "
                    "keinem Kunden gegenueber als gesetzt."
                )
            )
        record = await write_memory(
            db,
            tenant_id=tenant_id,
            agent=agent,
            tier=str(tc.arguments.get("tier", "")),
            content=str(tc.arguments.get("content", "")),
            metadata={"task_id": str(task.id)},
        )
        return ControlOutcome(output=f"memory recorded ({record.id})")

    if tc.name == DELEGATE_TASK.name:
        if decision.effect is Effect.DENY:
            if decision.reason == DEPTH_LIMIT_REASON:
                # Gate on the ACTUAL deny reason, not a re-derived depth check: an
                # unrelated DENY (self/empty/bad-uuid) on a task already at the cap
                # must not mislabel the audit trail as a depth breach. Make the
                # real runaway visible to an operator, not just the model.
                await record_activity(
                    db,
                    tenant_id=tenant_id,
                    agent_id=agent.id,
                    status="warning",
                    message=(
                        f"{agent.name} hit the delegation depth limit ({MAX_DELEGATION_DEPTH})"
                    ),
                )
            return ControlOutcome(output=f"ERROR: {decision.reason or 'delegation denied'}")
        output, sub_run_id = await _delegate(
            db, tenant_id=tenant_id, agent=agent, task=task, tc=tc, mcp_conn=mcp_conn
        )
        return ControlOutcome(output=output, pending_run=sub_run_id)

    if tc.name == RENDER_COMPONENT.name:
        component_key = str(tc.arguments.get("component_key", "")).strip()
        props_model = COMPONENT_CATALOG.get(component_key)
        if props_model is None:
            return ControlOutcome(output=f"ERROR: no such component '{component_key}'")
        if not await _has_component_grant(db, agent=agent, component_key=component_key):
            return ControlOutcome(
                output=f"ERROR: you have not been granted the '{component_key}' component"
            )
        raw_props = tc.arguments.get("props")
        if not isinstance(raw_props, dict):
            return ControlOutcome(output="ERROR: render_component requires a `props` object")
        try:
            props = props_model.model_validate(raw_props)
        except ValidationError as exc:
            return ControlOutcome(
                output=f"ERROR: invalid props for '{component_key}': {exc}"
            )
        await append_event(
            db,
            tenant_id=tenant_id,
            actor_type="agent",
            actor_id=agent.id,
            category="tool_action",
            action="component.rendered",
            resource={
                "agent_id": str(agent.id),
                "task_id": str(task.id),
                "component_key": component_key,
            },
            originating_operator=originating_operator,
        )
        await record_activity(
            db,
            tenant_id=tenant_id,
            agent_id=agent.id,
            status="info",
            message=f"Zeigt Karte: {component_key}",
        )
        return ControlOutcome(
            output=f"Rendered the '{component_key}' card for the human.",
            rendered_component={
                "component_key": component_key,
                "props": props.model_dump(mode="json"),
            },
        )

    if tc.name in skill_by_tool:
        skill = skill_by_tool[tc.name]
        if skill in active_skills:
            return ControlOutcome(output=f"Skill '{skill.name}' is already active.")
        await append_event(
            db,
            tenant_id=tenant_id,
            actor_type="agent",
            actor_id=agent.id,
            category="tool_action",
            action="skill.invoked",
            resource={
                "skill_id": str(skill.skill_id),
                "skill_version_id": str(skill.skill_version_id),
                "agent_id": str(agent.id),
                "task_id": str(task.id),
            },
            originating_operator=originating_operator,
        )
        await record_activity(
            db,
            tenant_id=tenant_id,
            agent_id=agent.id,
            status="info",
            message=f"Skill activated: {skill.name}",
        )
        # The procedure goes in the TOOL RESULT, not into a separate system
        # message. Invoking a skill is a tool call, so its instruction is simply
        # what that call returned -- and a mid-conversation system message is
        # rejected outright by strict backends ("Unexpected role 'system' after
        # role 'tool'", verified against a hosted vLLM behind LiteLLM), which
        # killed the run on the next step. This way the ordering hazard cannot
        # exist: there is no extra message to place.
        return ControlOutcome(
            output=(
                f"Skill '{skill.name}' activated. Follow this procedure:\n\n"
                f"{instruction_block(skill)}"
            ),
            activated_skill=skill,
        )

    return None
