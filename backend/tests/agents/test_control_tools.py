"""The control-plane tools the core itself owns, shared by both runtimes.

These are the tools that are NOT a connection's MCP tools: remember something,
ask the operator, delegate, invoke a skill. They used to be defined inline in
engine.py's run loop, so the isolated runtime offered none of them.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.control_tools import (
    CONTROL_TOOL_NAMES,
    execute_control_tool,
    offered_tools,
)
from oc8.authz.pdp import Decision, Effect
from oc8.modelrouter import NeutralTool, ToolCall
from oc8.skills.runtime import LoadedSkill
from oc8.skills.schema import parse_definition

MCP_TOOLS = [
    NeutralTool(name="create_record", description="", parameters={}),
    NeutralTool(name="read_record", description="", parameters={}),
    NeutralTool(name="send_email", description="", parameters={}),
]


def _agent(*, is_team_lead: bool = False) -> m.Agent:
    return m.Agent(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), department_id=uuid.uuid4(),
        name="Nora", status="idle", definition={}, presentation={},
        is_team_lead=is_team_lead,
    )


def _skill(tool_name: str, *, requires: list[str]) -> LoadedSkill:
    definition: dict[str, Any] = {
        "oc8_skill": 1,
        "id": "sk-x",
        "version": "1.0.0",
        "instruction": "Do the thing.",
        "requires": {"tools": [{"tool": t, "rights": ["read"]} for t in requires], "kbs": []},
        "guardrails": [],
    }
    return LoadedSkill(
        skill_id=uuid.uuid4(), skill_version_id=uuid.uuid4(), name="Skill X",
        description="d", tool_name=tool_name,
        definition=parse_definition(definition), creator_id=None,
    )


def test_every_agent_may_remember_and_ask() -> None:
    names = [t.name for t in offered_tools(
        _agent(), assigned_skills=[], active_skills=[], mcp_tools=MCP_TOOLS
    )]
    assert "memory_write" in names
    assert "ask_user" in names


def test_only_a_team_lead_is_offered_delegation() -> None:
    """Offering delegate_task to a non-lead would be offering a tool that
    _authorize denies on every call -- pure model confusion."""
    plain = [t.name for t in offered_tools(
        _agent(), assigned_skills=[], active_skills=[], mcp_tools=MCP_TOOLS
    )]
    lead = [t.name for t in offered_tools(
        _agent(is_team_lead=True), assigned_skills=[], active_skills=[], mcp_tools=MCP_TOOLS
    )]
    assert "delegate_task" not in plain
    assert "delegate_task" in lead


def test_an_active_skill_narrows_the_connection_tools() -> None:
    """An active skill focuses the model on its own tools. OFFERING only --
    _authorize still checks the frame on every call, so this cannot widen."""
    skill = _skill("skill_x", requires=["read_record"])
    names = [t.name for t in offered_tools(
        _agent(), assigned_skills=[skill], active_skills=[skill], mcp_tools=MCP_TOOLS
    )]
    assert "read_record" in names
    assert "create_record" not in names
    assert "send_email" not in names


def test_a_skill_requiring_nothing_available_falls_back_to_all_tools() -> None:
    """A skill whose required tools this connection does not have must not leave
    the model with no connection tools at all -- it would be unable to act."""
    skill = _skill("skill_x", requires=["nonexistent_tool"])
    names = [t.name for t in offered_tools(
        _agent(), assigned_skills=[skill], active_skills=[skill], mcp_tools=MCP_TOOLS
    )]
    for t in MCP_TOOLS:
        assert t.name in names


def test_an_assigned_skill_stays_offered_once_active() -> None:
    """Withdrawing the tool the moment it activates would strand a model that
    re-checks its own tool list with an unknown tool name."""
    skill = _skill("skill_x", requires=["read_record"])
    names = [t.name for t in offered_tools(
        _agent(), assigned_skills=[skill], active_skills=[skill], mcp_tools=MCP_TOOLS
    )]
    assert "skill_x" in names


def test_without_an_active_skill_all_connection_tools_are_offered() -> None:
    names = [t.name for t in offered_tools(
        _agent(), assigned_skills=[], active_skills=[], mcp_tools=MCP_TOOLS
    )]
    for t in MCP_TOOLS:
        assert t.name in names


def test_control_tool_names_matches_the_schemas() -> None:
    assert CONTROL_TOOL_NAMES == {
        "memory_write",
        "ask_user",
        "delegate_task",
        "request_decision",
        "search_knowledge",
        "search_memory",
        "render_component",
    }


def test_render_component_is_offered_to_every_agent() -> None:
    """Withholding it would not be a real gate -- the ComponentGrant check
    inside execute_control_tool is the gate; offering only hides, and "a
    grant that only hides is not a grant that holds" (see pdp.py)."""
    names = [t.name for t in offered_tools(
        _agent(), assigned_skills=[], active_skills=[], mcp_tools=MCP_TOOLS
    )]
    assert "render_component" in names


# ------------------------------------------------------------------ execution


async def _dept_agent_task(
    db: Any, tenant: uuid.UUID, *, is_team_lead: bool = False, depth: int = 0
) -> tuple[m.Agent, m.Task]:
    dept = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
    db.add(dept)
    await db.flush()
    agent = m.Agent(
        tenant_id=tenant, department_id=dept.id, name="Nora", status="running",
        definition={}, presentation={}, narrowing={}, is_team_lead=is_team_lead,
    )
    db.add(agent)
    await db.flush()
    task = m.Task(
        tenant_id=tenant, department_id=dept.id, assigned_agent_id=agent.id,
        title="Erstelle ein Angebot", state="in_progress", delegation_depth=depth,
    )
    db.add(task)
    await db.flush()
    return agent, task


@pytest.mark.asyncio
async def test_ask_user_asks_to_suspend(app_session: Any) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db, tenant_id=tenant, agent=agent, task=task,
            tc=ToolCall(id="c1", name="ask_user", arguments={"question": "Welches Konto?"}),
            decision=Decision(Effect.ALLOW), assigned_skills=[], active_skills=[],
            mcp_conn=None, originating_operator=None,
        )
        assert outcome is not None
    assert outcome.suspend == "waiting_for_input"
    assert outcome.output == "Welches Konto?"


@pytest.mark.asyncio
async def test_an_empty_question_is_a_model_error_not_a_suspend(app_session: Any) -> None:
    """Suspending on an empty question would park the run on a question no human
    can answer -- it is the model that erred, so tell the model."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db, tenant_id=tenant, agent=agent, task=task,
            tc=ToolCall(id="c1", name="ask_user", arguments={"question": "   "}),
            decision=Decision(Effect.ALLOW), assigned_skills=[], active_skills=[],
            mcp_conn=None, originating_operator=None,
        )
        assert outcome is not None
    assert outcome.suspend is None
    assert outcome.output.startswith("ERROR:")


@pytest.mark.asyncio
async def test_memory_write_records_and_reports_the_id(app_session: Any) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db, tenant_id=tenant, agent=agent, task=task,
            tc=ToolCall(id="c1", name="memory_write",
                        arguments={"tier": "agent", "content": "Kunde zahlt per Rechnung."}),
            decision=Decision(Effect.ALLOW), assigned_skills=[], active_skills=[],
            mcp_conn=None, originating_operator=None,
        )
        assert outcome is not None
        assert "memory recorded" in outcome.output
        rows = (
            await db.execute(
                m.MemoryRecord.__table__.select().where(m.MemoryRecord.tenant_id == tenant)
            )
        ).fetchall()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_a_denied_memory_write_reaches_the_model_as_an_error(app_session: Any) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db, tenant_id=tenant, agent=agent, task=task,
            tc=ToolCall(id="c1", name="memory_write",
                        arguments={"tier": "company", "content": "x"}),
            decision=Decision(Effect.DENY, "company tier not permitted"),
            assigned_skills=[], active_skills=[], mcp_conn=None, originating_operator=None,
        )
        assert outcome is not None
        assert outcome.output == "ERROR: company tier not permitted"
        rows = (
            await db.execute(
                m.MemoryRecord.__table__.select().where(m.MemoryRecord.tenant_id == tenant)
            )
        ).fetchall()
        assert rows == []


@pytest.mark.asyncio
async def test_delegate_task_creates_a_sub_run_the_caller_must_publish(app_session: Any) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        lead, task = await _dept_agent_task(db, tenant, is_team_lead=True)
        mate = m.Agent(
            tenant_id=tenant, department_id=lead.department_id, name="Rico",
            status="idle", definition={}, presentation={},
        )
        db.add(mate)
        await db.flush()
        outcome = await execute_control_tool(
            db, tenant_id=tenant, agent=lead, task=task,
            tc=ToolCall(id="c1", name="delegate_task",
                        arguments={"agent_id": str(mate.id), "task_text": "Ruf den Kunden an"}),
            decision=Decision(Effect.ALLOW), assigned_skills=[], active_skills=[],
            mcp_conn=None, originating_operator=None,
        )
        assert outcome is not None
        assert outcome.pending_run is not None
        assert "Rico" in outcome.output
        sub = await db.get(m.AgentRun, outcome.pending_run)
        assert sub is not None
        # The sub-run carries the hop count, so the depth cap can actually bite.
        assert sub.context["delegation_depth"] == 1
        assert sub.context["parent_task_id"] == str(task.id)


@pytest.mark.asyncio
async def test_delegation_to_another_department_is_refused(app_session: Any) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        lead, task = await _dept_agent_task(db, tenant, is_team_lead=True)
        other = m.Department(tenant_id=tenant, name="Buchhaltung", frame={})
        db.add(other)
        await db.flush()
        stranger = m.Agent(
            tenant_id=tenant, department_id=other.id, name="Fremd", status="idle",
            definition={}, presentation={},
        )
        db.add(stranger)
        await db.flush()
        outcome = await execute_control_tool(
            db, tenant_id=tenant, agent=lead, task=task,
            tc=ToolCall(id="c1", name="delegate_task",
                        arguments={"agent_id": str(stranger.id), "task_text": "x"}),
            decision=Decision(Effect.ALLOW), assigned_skills=[], active_skills=[],
            mcp_conn=None, originating_operator=None,
        )
        assert outcome is not None
        assert outcome.pending_run is None
        assert "your own department" in outcome.output


@pytest.mark.asyncio
async def test_the_depth_limit_deny_warns_an_operator(app_session: Any) -> None:
    """A runaway delegation chain must be visible to a human, not only to the
    model that tripped it."""
    from oc8.agent.control_tools import DEPTH_LIMIT_REASON

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        lead, task = await _dept_agent_task(db, tenant, is_team_lead=True)
        outcome = await execute_control_tool(
            db, tenant_id=tenant, agent=lead, task=task,
            tc=ToolCall(id="c1", name="delegate_task",
                        arguments={"agent_id": str(uuid.uuid4()), "task_text": "x"}),
            decision=Decision(Effect.DENY, DEPTH_LIMIT_REASON),
            assigned_skills=[], active_skills=[], mcp_conn=None, originating_operator=None,
        )
        assert outcome is not None
        assert outcome.output.startswith("ERROR:")
        events = (
            await db.execute(
                m.ActivityEvent.__table__.select().where(
                    m.ActivityEvent.tenant_id == tenant,
                    m.ActivityEvent.status == "warning",
                )
            )
        ).fetchall()
        assert len(events) == 1


@pytest.mark.asyncio
async def test_an_unrelated_delegation_deny_does_not_warn(app_session: Any) -> None:
    """Gated on the ACTUAL deny reason: an unrelated DENY on a task already at
    the cap must not mislabel the audit trail as a depth breach."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        lead, task = await _dept_agent_task(db, tenant, is_team_lead=True, depth=5)
        await execute_control_tool(
            db, tenant_id=tenant, agent=lead, task=task,
            tc=ToolCall(id="c1", name="delegate_task", arguments={"agent_id": "nope"}),
            decision=Decision(Effect.DENY, "invalid agent_id: 'nope'"),
            assigned_skills=[], active_skills=[], mcp_conn=None, originating_operator=None,
        )
        events = (
            await db.execute(
                m.ActivityEvent.__table__.select().where(
                    m.ActivityEvent.tenant_id == tenant,
                    m.ActivityEvent.status == "warning",
                )
            )
        ).fetchall()
        assert events == []


@pytest.mark.asyncio
async def test_invoking_a_skill_activates_it(app_session: Any) -> None:
    tenant = uuid.uuid4()
    skill = _skill("skill_x", requires=["read_record"])
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db, tenant_id=tenant, agent=agent, task=task,
            tc=ToolCall(id="c1", name="skill_x", arguments={}),
            decision=Decision(Effect.ALLOW), assigned_skills=[skill], active_skills=[],
            mcp_conn=None, originating_operator=None,
        )
        assert outcome is not None
    assert outcome.activated_skill is skill
    assert "activated" in outcome.output


@pytest.mark.asyncio
async def test_re_invoking_an_active_skill_is_a_harmless_no_op(app_session: Any) -> None:
    tenant = uuid.uuid4()
    skill = _skill("skill_x", requires=["read_record"])
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db, tenant_id=tenant, agent=agent, task=task,
            tc=ToolCall(id="c1", name="skill_x", arguments={}),
            decision=Decision(Effect.ALLOW), assigned_skills=[skill], active_skills=[skill],
            mcp_conn=None, originating_operator=None,
        )
        assert outcome is not None
    assert outcome.activated_skill is None
    assert "already active" in outcome.output


@pytest.mark.asyncio
async def test_a_non_control_tool_is_not_handled_here(app_session: Any) -> None:
    """The dispatcher must say 'not mine' for a connection tool, so the caller
    routes it to the MCP server instead of silently swallowing it."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db, tenant_id=tenant, agent=agent, task=task,
            tc=ToolCall(id="c1", name="create_record", arguments={}),
            decision=Decision(Effect.ALLOW), assigned_skills=[], active_skills=[],
            mcp_conn=None, originating_operator=None,
        )
    assert outcome is None


@pytest.mark.parametrize("name", sorted(CONTROL_TOOL_NAMES))
def test_each_control_tool_declares_its_required_arguments(name: str) -> None:
    """A tool offered without a schema the model can satisfy is a tool the model
    will call wrongly."""
    from oc8.agent.control_tools import CONTROL_TOOL_SCHEMAS

    schema = CONTROL_TOOL_SCHEMAS[name]
    assert schema.description
    assert schema.parameters["required"]


@pytest.mark.asyncio
async def test_in_process_skill_instruction_lands_after_the_tool_result(
    app_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same invariant as the isolated path: a tool result must directly follow the
    assistant message that requested it. engine.py used to append an activated
    skill's instruction BEFORE the result, which a strict OpenAI-compatible
    provider rejects with 400 -- so the run died one step later. Never surfaced
    until an agent actually had a skill."""
    from oc8.agent.engine import run_agent
    from oc8.modelrouter import CompletionResult, ToolCall, Usage, chunk_from_result
    from oc8.skills.schema import parse_definition

    DEF = {
        "oc8_skill": 1, "id": "sk-x", "version": "1.0.0",
        "instruction": "FOLGE DIESEM VERFAHREN.",
        "requires": {"tools": [], "kbs": []}, "guardrails": [],
    }

    seen: list[list[str]] = []

    class _InvokesSkillThenStops:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, req: Any) -> CompletionResult:
            self.calls += 1
            seen.append([msg.role for msg in req.messages])
            if self.calls == 1:
                return CompletionResult(
                    text="", tool_calls=[ToolCall(id="c1", name="skill_sk_x", arguments={})],
                    usage=Usage(1, 1), stop_reason="tool_use", provider="ollama", model="m",
                )
            return CompletionResult(
                text="fertig", tool_calls=[], usage=Usage(1, 1),
                stop_reason="stop", provider="ollama", model="m",
            )

        async def stream(self, req: Any) -> Any:
            yield chunk_from_result(await self.complete(req))

    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: _InvokesSkillThenStops())

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant, department_id=dept.id, name="Nora", status="running",
            narrowing={}, definition={}, presentation={},
        )
        skill = m.Skill(tenant_id=tenant, name="X", description="d", author="t")
        db.add_all([agent, skill])
        await db.flush()
        version = m.SkillVersion(
            tenant_id=tenant, skill_id=skill.id, semver="1.0.0",
            definition=DEF, artifact_hash=b"\x00" * 32,
        )
        db.add(version)
        await db.flush()
        skill.current_version_id = version.id
        db.add(m.SkillAssignment(
            tenant_id=tenant, agent_id=agent.id, skill_version_id=version.id, enabled=True
        ))
        await db.flush()
        _ = parse_definition(DEF)
        await run_agent(db, agent=agent, task_text="mach ein Angebot", tenant_id=tenant)

    # The SECOND model call is the one that would have been rejected.
    assert len(seen) >= 2, seen
    roles = seen[1]
    i = roles.index("assistant")
    assert roles[i + 1] == "tool", f"a tool result must follow its call directly, got {roles}"
    # And nothing follows it -- no injected system message to be misplaced.
    assert "system" not in roles[i + 1 :], f"no message may be inserted, got {roles}"


@pytest.mark.asyncio
async def test_render_component_is_refused_without_a_grant(app_session: Any) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db, tenant_id=tenant, agent=agent, task=task,
            tc=ToolCall(
                id="c1", name="render_component",
                arguments={"component_key": "record_card", "props": {"title": "x"}},
            ),
            decision=Decision(Effect.ALLOW), assigned_skills=[], active_skills=[],
            mcp_conn=None, originating_operator=None,
        )
        assert outcome is not None
    assert outcome.output.startswith("ERROR:")
    assert outcome.rendered_component is None


@pytest.mark.asyncio
async def test_render_component_rejects_an_unknown_component_key(app_session: Any) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        db.add(m.ComponentGrant(
            tenant_id=tenant, component_key="record_card",
            grantee_type="agent", grantee_id=agent.id,
        ))
        await db.flush()
        outcome = await execute_control_tool(
            db, tenant_id=tenant, agent=agent, task=task,
            tc=ToolCall(
                id="c1", name="render_component",
                arguments={"component_key": "nope", "props": {}},
            ),
            decision=Decision(Effect.ALLOW), assigned_skills=[], active_skills=[],
            mcp_conn=None, originating_operator=None,
        )
        assert outcome is not None
    assert outcome.output == "ERROR: no such component 'nope'"


@pytest.mark.asyncio
async def test_render_component_rejects_invalid_props(app_session: Any) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        db.add(m.ComponentGrant(
            tenant_id=tenant, component_key="record_card",
            grantee_type="agent", grantee_id=agent.id,
        ))
        await db.flush()
        outcome = await execute_control_tool(
            db, tenant_id=tenant, agent=agent, task=task,
            tc=ToolCall(
                id="c1", name="render_component",
                arguments={"component_key": "record_card", "props": {"no_title": "x"}},
            ),
            decision=Decision(Effect.ALLOW), assigned_skills=[], active_skills=[],
            mcp_conn=None, originating_operator=None,
        )
        assert outcome is not None
    assert outcome.output.startswith("ERROR: invalid props for 'record_card'")


@pytest.mark.asyncio
async def test_a_granted_agent_can_render_a_record_card(app_session: Any) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        db.add(m.ComponentGrant(
            tenant_id=tenant, component_key="record_card",
            grantee_type="agent", grantee_id=agent.id,
        ))
        await db.flush()
        outcome = await execute_control_tool(
            db, tenant_id=tenant, agent=agent, task=task,
            tc=ToolCall(
                id="c1", name="render_component",
                arguments={
                    "component_key": "record_card",
                    "props": {"title": "Acme GmbH — 12.400 €"},
                },
            ),
            decision=Decision(Effect.ALLOW), assigned_skills=[], active_skills=[],
            mcp_conn=None, originating_operator=None,
        )
        assert outcome is not None
    assert not outcome.output.startswith("ERROR")
    assert outcome.rendered_component == {
        "component_key": "record_card",
        "props": {
            "title": "Acme GmbH — 12.400 €",
            "subtitle": None,
            "fields": [],
            "link_label": None,
            "link_url": None,
        },
    }


@pytest.mark.asyncio
async def test_a_department_wide_grant_covers_every_agent_in_it(app_session: Any) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        db.add(m.ComponentGrant(
            tenant_id=tenant, component_key="record_card",
            grantee_type="department", grantee_id=agent.department_id,
        ))
        await db.flush()
        outcome = await execute_control_tool(
            db, tenant_id=tenant, agent=agent, task=task,
            tc=ToolCall(
                id="c1", name="render_component",
                arguments={"component_key": "record_card", "props": {"title": "x"}},
            ),
            decision=Decision(Effect.ALLOW), assigned_skills=[], active_skills=[],
            mcp_conn=None, originating_operator=None,
        )
        assert outcome is not None
    assert not outcome.output.startswith("ERROR")


@pytest.mark.asyncio
async def test_run_agent_publishes_a_rendered_component(
    app_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """render_component's effect is DATA on the outcome (see control_tools.py);
    it is run_agent's job to turn that into the realtime event the frontend
    actually consumes."""
    from oc8.agent.engine import run_agent
    from oc8.modelrouter import CompletionResult, ToolCall, Usage, chunk_from_result

    published: list[tuple[str, dict[str, Any]]] = []

    class _SpyBus:
        async def publish_event(
            self, tenant_id: Any, type_: str, data: dict[str, Any], **kw: Any
        ) -> None:
            published.append((type_, data))

    monkeypatch.setattr("oc8.realtime.bus.get_event_bus", lambda: _SpyBus())

    class _RendersThenStops:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, req: Any) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                return CompletionResult(
                    text="", tool_calls=[ToolCall(
                        id="c1", name="render_component",
                        arguments={"component_key": "record_card", "props": {"title": "x"}},
                    )],
                    usage=Usage(1, 1), stop_reason="tool_use", provider="ollama", model="m",
                )
            return CompletionResult(
                text="fertig", tool_calls=[], usage=Usage(1, 1),
                stop_reason="stop", provider="ollama", model="m",
            )

        async def stream(self, req: Any) -> Any:
            yield chunk_from_result(await self.complete(req))

    monkeypatch.setattr("oc8.agent.engine.get_model_router", lambda: _RendersThenStops())

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, _task = await _dept_agent_task(db, tenant)
        db.add(m.ComponentGrant(
            tenant_id=tenant, component_key="record_card",
            grantee_type="agent", grantee_id=agent.id,
        ))
        run = m.AgentRun(tenant_id=tenant, agent_id=agent.id, state="running", context={})
        db.add(run)
        await db.flush()
        await run_agent(
            db, agent=agent, task_text="mach ein Angebot", tenant_id=tenant, run_id=run.id
        )

    assert (
        "run.component_rendered",
        {
            "run_id": str(run.id),
            "component_key": "record_card",
            "props": {
                "title": "x", "subtitle": None, "fields": [],
                "link_label": None, "link_url": None,
            },
        },
    ) in published


@pytest.mark.asyncio
async def test_a_direct_and_a_department_grant_can_coexist(app_session: Any) -> None:
    """ComponentGrant's own UniqueConstraint allows one row per grantee_type,
    so an agent can legitimately hold BOTH a direct grant and a department-wide
    grant for the same component_key at once -- this must not crash the grant
    check with MultipleResultsFound."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        db.add(m.ComponentGrant(
            tenant_id=tenant, component_key="record_card",
            grantee_type="agent", grantee_id=agent.id,
        ))
        db.add(m.ComponentGrant(
            tenant_id=tenant, component_key="record_card",
            grantee_type="department", grantee_id=agent.department_id,
        ))
        await db.flush()
        outcome = await execute_control_tool(
            db, tenant_id=tenant, agent=agent, task=task,
            tc=ToolCall(
                id="c1", name="render_component",
                arguments={"component_key": "record_card", "props": {"title": "x"}},
            ),
            decision=Decision(Effect.ALLOW), assigned_skills=[], active_skills=[],
            mcp_conn=None, originating_operator=None,
        )
        assert outcome is not None
    assert not outcome.output.startswith("ERROR")
