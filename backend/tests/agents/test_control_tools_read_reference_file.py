"""read_reference_file: an agent may read a file under an ASSIGNED skill's
own references/assets/scripts directory (capas.manifest.SkillTemplateSpec
.reference_root), resolved via capas.discovery.find_plugin at call time --
never any other skill's files, never anything outside those three
directories, never a path that escapes the skill's own directory."""

from __future__ import annotations

import types
import uuid
from pathlib import Path
from typing import Any

import pytest

from oc8 import models as m
from oc8.agent.control_tools import (
    READ_REFERENCE_FILE,
    execute_control_tool,
    offered_tools,
)
from oc8.authz.pdp import Decision, Effect
from oc8.modelrouter import NeutralTool, ToolCall
from oc8.skills.runtime import LoadedSkill
from oc8.skills.schema import parse_definition

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


def _skill(name: str, *, reference_root: str | None) -> LoadedSkill:
    definition: dict[str, Any] = {
        "oc8_skill": 1,
        "id": "sk-x",
        "version": "1.0.0",
        "instruction": "Do the thing.",
        "requires": {"tools": [], "kbs": []},
        "guardrails": [],
        "reference_root": reference_root,
    }
    return LoadedSkill(
        skill_id=uuid.uuid4(),
        skill_version_id=uuid.uuid4(),
        name=name,
        description="d",
        tool_name="skill_x",
        definition=parse_definition(definition),
        creator_id=None,
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


def _capa_folder(tmp_path: Path) -> Path:
    """A capa on disk with one skill's references/ under skills/thai-compliance/,
    mirroring what capas/discovery.py + capas/materialise.py actually produce."""
    folder = tmp_path / "compliance_plugin"
    refs = folder / "skills" / "thai-compliance" / "references"
    refs.mkdir(parents=True)
    (refs / "checklist.md").write_text("1. Check VAT ID.\n2. Check address.\n")
    (folder / "skills" / "thai-compliance" / "scripts").mkdir()
    (folder / "skills" / "thai-compliance" / "scripts" / "run.sh").write_text("echo hi\n")
    return folder


def _patch_find_plugin(
    monkeypatch: pytest.MonkeyPatch, folder: Path, *, valid: bool = True
) -> None:
    plugin = types.SimpleNamespace(valid=valid, path=str(folder))
    monkeypatch.setattr(
        "oc8.agent.control_tools.find_plugin", lambda name: plugin if valid else None
    )


# ------------------------------------------------------------------ offered_tools


def test_not_offered_when_no_assigned_skill_has_a_reference_root() -> None:
    skill = _skill("Compliance", reference_root=None)
    names = [
        t.name
        for t in offered_tools(
            _agent(), assigned_skills=[skill], active_skills=[], mcp_tools=MCP_TOOLS
        )
    ]
    assert READ_REFERENCE_FILE.name not in names


def test_offered_when_an_assigned_skill_has_a_reference_root() -> None:
    skill = _skill("Compliance", reference_root="compliance_plugin/skills/thai-compliance")
    names = [
        t.name
        for t in offered_tools(
            _agent(), assigned_skills=[skill], active_skills=[], mcp_tools=MCP_TOOLS
        )
    ]
    assert READ_REFERENCE_FILE.name in names


# ------------------------------------------------------------------ execution


@pytest.mark.asyncio
async def test_reads_a_reference_file_for_an_assigned_skill(
    app_session: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _capa_folder(tmp_path)
    _patch_find_plugin(monkeypatch, folder)
    skill = _skill("Compliance", reference_root="compliance_plugin/skills/thai-compliance")
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(
                id="c1",
                name="read_reference_file",
                arguments={"skill": "Compliance", "path": "references/checklist.md"},
            ),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[skill],
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert "Check VAT ID" in outcome.output


@pytest.mark.asyncio
async def test_reads_a_scripts_file_too(
    app_session: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _capa_folder(tmp_path)
    _patch_find_plugin(monkeypatch, folder)
    skill = _skill("Compliance", reference_root="compliance_plugin/skills/thai-compliance")
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(
                id="c1",
                name="read_reference_file",
                arguments={"skill": "Compliance", "path": "scripts/run.sh"},
            ),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[skill],
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert "echo hi" in outcome.output


@pytest.mark.asyncio
async def test_rejects_a_skill_not_assigned_to_this_agent(
    app_session: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _capa_folder(tmp_path)
    _patch_find_plugin(monkeypatch, folder)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(
                id="c1",
                name="read_reference_file",
                arguments={"skill": "Some Other Skill", "path": "references/checklist.md"},
            ),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[],  # nothing assigned at all
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert outcome.output.startswith("ERROR")
    assert "not one of your assigned skills" in outcome.output


@pytest.mark.asyncio
async def test_rejects_a_skill_with_no_reference_root(
    app_session: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _capa_folder(tmp_path)
    _patch_find_plugin(monkeypatch, folder)
    skill = _skill("Compliance", reference_root=None)
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(
                id="c1",
                name="read_reference_file",
                arguments={"skill": "Compliance", "path": "references/checklist.md"},
            ),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[skill],
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert "has no reference files" in outcome.output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_path",
    [
        "plugin.toml",
        "guardrails/library.toml",
        "setup/mcp.toml",
        "../../etc/passwd",
        "references/../../../etc/passwd",
    ],
)
async def test_rejects_a_path_outside_the_allowed_subdirectories(
    app_session: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_path: str
) -> None:
    folder = _capa_folder(tmp_path)
    _patch_find_plugin(monkeypatch, folder)
    skill = _skill("Compliance", reference_root="compliance_plugin/skills/thai-compliance")
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(
                id="c1",
                name="read_reference_file",
                arguments={"skill": "Compliance", "path": bad_path},
            ),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[skill],
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert outcome.output.startswith("ERROR")


@pytest.mark.asyncio
async def test_missing_file_is_a_plain_error_not_an_exception(
    app_session: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _capa_folder(tmp_path)
    _patch_find_plugin(monkeypatch, folder)
    skill = _skill("Compliance", reference_root="compliance_plugin/skills/thai-compliance")
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(
                id="c1",
                name="read_reference_file",
                arguments={"skill": "Compliance", "path": "references/does-not-exist.md"},
            ),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[skill],
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert "no such file" in outcome.output


@pytest.mark.asyncio
async def test_capa_no_longer_installed_is_a_plain_error(
    app_session: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_find_plugin(monkeypatch, tmp_path, valid=False)
    skill = _skill("Compliance", reference_root="compliance_plugin/skills/thai-compliance")
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(
                id="c1",
                name="read_reference_file",
                arguments={"skill": "Compliance", "path": "references/checklist.md"},
            ),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[skill],
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert "not installed here" in outcome.output


@pytest.mark.asyncio
async def test_a_large_file_is_truncated_not_refused(
    app_session: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _capa_folder(tmp_path)
    big = folder / "skills" / "thai-compliance" / "references" / "big.md"
    big.write_text("x" * 70_000)
    _patch_find_plugin(monkeypatch, folder)
    skill = _skill("Compliance", reference_root="compliance_plugin/skills/thai-compliance")
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent, task = await _dept_agent_task(db, tenant)
        outcome = await execute_control_tool(
            db,
            tenant_id=tenant,
            agent=agent,
            task=task,
            tc=ToolCall(
                id="c1",
                name="read_reference_file",
                arguments={"skill": "Compliance", "path": "references/big.md"},
            ),
            decision=Decision(Effect.ALLOW),
            assigned_skills=[skill],
            active_skills=[],
            mcp_conn=None,
            originating_operator=None,
        )
    assert outcome is not None
    assert "[truncated" in outcome.output
    assert len(outcome.output) < 70_000
