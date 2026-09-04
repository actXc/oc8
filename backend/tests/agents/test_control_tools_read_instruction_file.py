"""read_instruction_file: an agent may read a file attached to its own
standing Instructions (FileAttachment(owner_type="agent_instructions",
owner_id=agent.id)) on demand -- never auto-injected into every run's
prompt, never resolves another agent's or another tenant's attachments,
and never returns an image's content (instruction attachments carry no
vision support, only chat attachments do)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.control_tools import (
    READ_INSTRUCTION_FILE,
    execute_control_tool,
    offered_tools,
)
from oc8.authz.pdp import Decision, Effect
from oc8.modelrouter import NeutralTool, ToolCall

MCP_TOOLS: list[NeutralTool] = []


def _agent() -> m.Agent:
    return m.Agent(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        department_id=uuid.uuid4(),
        name="Nora",
        status="idle",
        definition={},
        presentation={},
    )


async def _dept_agent_task(db: Any, tenant: uuid.UUID) -> tuple[m.Agent, m.Task]:
    dept = m.Department(tenant_id=tenant, name="Compliance", frame={})
    db.add(dept)
    await db.flush()
    agent = m.Agent(
        tenant_id=tenant,
        department_id=dept.id,
        name="Nora",
        status="running",
        definition={},
        presentation={},
        narrowing={},
    )
    db.add(agent)
    await db.flush()
    task = m.Task(
        tenant_id=tenant,
        department_id=dept.id,
        assigned_agent_id=agent.id,
        title="Check compliance",
        state="in_progress",
        delegation_depth=0,
    )
    db.add(task)
    await db.flush()
    return agent, task


# ------------------------------------------------------------------ offered_tools


def test_not_offered_without_has_instruction_files() -> None:
    names = [
        t.name
        for t in offered_tools(
            _agent(), assigned_skills=[], active_skills=[], mcp_tools=MCP_TOOLS
        )
    ]
    assert READ_INSTRUCTION_FILE.name not in names


def test_offered_once_has_instruction_files_is_true() -> None:
    names = [
        t.name
        for t in offered_tools(
            _agent(),
            assigned_skills=[],
            active_skills=[],
            mcp_tools=MCP_TOOLS,
            has_instruction_files=True,
        )
    ]
    assert READ_INSTRUCTION_FILE.name in names


# ------------------------------------------------------------------ execution


@pytest.mark.asyncio
async def test_read_instruction_file_resolves_a_real_attachment(app_session: Any) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        db.add(
            m.FileAttachment(
                tenant_id=tenant,
                owner_type="agent_instructions",
                owner_id=agent.id,
                bucket_key="policy-1",
                filename="policy.pdf",
                content_type="application/pdf",
                size_bytes=10,
                extracted_text="Refunds within 30 days.",
                is_image=False,
            )
        )
        await db.flush()
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(id="1", name="read_instruction_file", arguments={"filename": "policy.pdf"}),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[],
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert "Refunds within 30 days." in outcome.output


@pytest.mark.asyncio
async def test_read_instruction_file_refuses_an_image(app_session: Any) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        db.add(
            m.FileAttachment(
                tenant_id=tenant,
                owner_type="agent_instructions",
                owner_id=agent.id,
                bucket_key="photo-1",
                filename="photo.png",
                content_type="image/png",
                size_bytes=10,
                extracted_text=None,
                is_image=True,
            )
        )
        await db.flush()
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(id="1", name="read_instruction_file", arguments={"filename": "photo.png"}),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[],
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert "do not support vision" in outcome.output


@pytest.mark.asyncio
async def test_read_instruction_file_404s_an_unknown_filename(app_session: Any) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(id="1", name="read_instruction_file", arguments={"filename": "nope.pdf"}),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[],
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert "no such file" in outcome.output.lower()


@pytest.mark.asyncio
async def test_read_instruction_file_does_not_resolve_another_agents_attachment(
    app_session: Any,
) -> None:
    """owner_id scoping: a file attached to a different agent's Instructions
    in the SAME tenant must not resolve, even with a matching filename."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        other_agent, _ = await _dept_agent_task(db, tenant)
        db.add(
            m.FileAttachment(
                tenant_id=tenant,
                owner_type="agent_instructions",
                owner_id=other_agent.id,
                bucket_key="policy-other-agent",
                filename="policy.pdf",
                content_type="application/pdf",
                size_bytes=10,
                extracted_text="Not yours.",
                is_image=False,
            )
        )
        await db.flush()
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(id="1", name="read_instruction_file", arguments={"filename": "policy.pdf"}),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[],
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert "no such file" in outcome.output.lower()


@pytest.mark.asyncio
async def test_read_instruction_file_404s_a_foreign_tenants_file(app_session: Any) -> None:
    tenant = uuid.uuid4()
    other_tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
    async with app_session(other_tenant) as other_db:
        other_db.add(
            m.FileAttachment(
                tenant_id=other_tenant,
                owner_type="agent_instructions",
                owner_id=agent.id,
                bucket_key="secret-1",
                filename="secret.pdf",
                content_type="application/pdf",
                size_bytes=10,
                extracted_text="classified",
                is_image=False,
            )
        )
    async with app_session(tenant) as db:
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(id="1", name="read_instruction_file", arguments={"filename": "secret.pdf"}),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[],
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert "no such file" in outcome.output.lower()


@pytest.mark.asyncio
async def test_read_instruction_file_requires_a_filename(app_session: Any) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(id="1", name="read_instruction_file", arguments={}),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[],
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert outcome.output.startswith("ERROR")
    assert "requires" in outcome.output


@pytest.mark.asyncio
async def test_a_large_instruction_file_is_truncated_not_refused(app_session: Any) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        db.add(
            m.FileAttachment(
                tenant_id=tenant,
                owner_type="agent_instructions",
                owner_id=agent.id,
                bucket_key="big-1",
                filename="big.txt",
                content_type="text/plain",
                size_bytes=70_000,
                extracted_text="x" * 70_000,
                is_image=False,
            )
        )
        await db.flush()
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(id="1", name="read_instruction_file", arguments={"filename": "big.txt"}),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[],
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert len(outcome.output) == 60_000


@pytest.mark.asyncio
async def test_an_attachment_with_no_extracted_text_reports_that_plainly(
    app_session: Any,
) -> None:
    """A row can exist with extracted_text=None even for a non-image
    (extraction can fail) -- the model must see that plainly, not an
    empty string it might mistake for "the file is empty"."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        db.add(
            m.FileAttachment(
                tenant_id=tenant,
                owner_type="agent_instructions",
                owner_id=agent.id,
                bucket_key="scan-1",
                filename="scan.pdf",
                content_type="application/pdf",
                size_bytes=10,
                extracted_text=None,
                is_image=False,
            )
        )
        await db.flush()
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(id="1", name="read_instruction_file", arguments={"filename": "scan.pdf"}),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[],
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert "could not read" in outcome.output


@pytest.mark.asyncio
async def test_read_instruction_file_survives_two_files_with_the_same_name(
    app_session: Any,
) -> None:
    """Nothing makes `filename` unique per agent, and attachments have no
    versioning -- so re-uploading a corrected `policy.pdf` without deleting
    the old one leaves two rows. That used to raise MultipleResultsFound,
    which neither dispatcher catches: it killed the run in-process and 500'd
    `/internal/runs/{id}/tool` in the container. The newest copy wins."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        older = m.FileAttachment(
            tenant_id=tenant,
            owner_type="agent_instructions",
            owner_id=agent.id,
            bucket_key="policy-old",
            filename="policy.pdf",
            content_type="application/pdf",
            size_bytes=10,
            extracted_text="Refunds within 14 days.",
            is_image=False,
            created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        )
        newer = m.FileAttachment(
            tenant_id=tenant,
            owner_type="agent_instructions",
            owner_id=agent.id,
            bucket_key="policy-new",
            filename="policy.pdf",
            content_type="application/pdf",
            size_bytes=10,
            extracted_text="Refunds within 30 days.",
            is_image=False,
            created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
        )
        db.add_all([older, newer])
        await db.flush()
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(id="1", name="read_instruction_file", arguments={"filename": "policy.pdf"}),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[],
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert "Refunds within 30 days." in outcome.output
