"""Authoring skills (§6.5): create, version, and manage agent assignments.

`GET /skills` (the catalog) and `POST /agents/{id}/skills` (assign) already
existed; what was missing was any way to *author* one. Until this module a skill
could only arrive by seeding, so the create wizard in the UI produced nothing but
local state.

The definition written here is the real one the runtime loads -- the tools the
author names become `requires.tools`, which the assignment endpoint enforces
against the agent's frame ∩ narrowing. A `presentation` block carries the
display-only copies the catalogue DTO reads.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select

from oc8 import models as m
from oc8.api.deps import CurrentPrincipal, DbSession, require_permission
from oc8.api.v1._serializers import skill_to_dto
from oc8.authz.permissions import MANAGE, SKILL, VIEW, perm
from oc8.db.base import uuid7
from oc8.knowledge.connectors.base import ConnectorError
from oc8.schemas.base import CamelModel
from oc8.schemas.dto import SkillDTO
from oc8.skills.importer import DEFAULT_BUDGET_TOKENS, discover
from oc8.skills.schema import SkillDefinitionError, parse_definition

router = APIRouter()


INITIAL_SEMVER = "0.1.0"


class CreateSkillRequest(CamelModel):
    name: str
    description: str = ""
    category: str = ""
    instructions: str
    tools: list[str] = []
    knowledge: list[str] = []
    guardrails: list[str] = []
    semver: str = INITIAL_SEMVER


class CreateVersionRequest(CamelModel):
    semver: str
    instructions: str
    tools: list[str] = []
    knowledge: list[str] = []
    guardrails: list[str] = []


class SkillAssignmentDTO(CamelModel):
    id: str
    agent_id: str
    skill_id: str
    skill_name: str
    skill_version_id: str
    semver: str
    enabled: bool


def _definition(
    *,
    slug: str,
    semver: str,
    instructions: str,
    tools: list[str],
    knowledge: list[str],
    guardrails: list[str],
    reference_root: str | None = None,
) -> dict[str, Any]:
    """Build the pinned definition shape.

    `requires` is load-bearing -- assignment checks it against the agent's
    effective rights -- while `presentation` is display-only and never gates
    anything.
    """
    return {
        "schema_version": 1,
        "slug": slug,
        "version": semver,
        "instruction": instructions.strip(),
        "requires": {"tools": list(tools), "kbs": list(knowledge)},
        "guardrails": list(guardrails),
        "reference_root": reference_root,
        "presentation": {
            "tools": list(tools),
            "knowledge": list(knowledge),
            "guardrails": list(guardrails),
            "updated_at": dt.datetime.now(tz=dt.UTC).date().isoformat(),
        },
    }


def _artifact_hash(definition: dict[str, Any]) -> bytes:
    return hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
    ).digest()


def _slugify(value: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "skill"


def _validated(definition: dict[str, Any]) -> dict[str, Any]:
    """Reject a definition the runtime could not load, at authoring time rather
    than at the next run."""
    try:
        parse_definition(definition)
    except SkillDefinitionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return definition


async def _load_skill(db: DbSession, skill_id: uuid.UUID, tenant_id: uuid.UUID) -> m.Skill:
    skill = await db.get(m.Skill, skill_id)
    # RLS already scopes this, but an explicit check keeps a cross-tenant id a
    # 404 rather than a 500 if the row is ever reachable another way.
    if skill is None or skill.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "skill not found")
    return skill


@router.post(
    "/skills",
    response_model=SkillDTO,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(perm(SKILL, MANAGE)))],
)
async def create_skill(
    body: CreateSkillRequest,
    db: DbSession,
    principal: CurrentPrincipal,
) -> SkillDTO:
    name = body.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name is required")

    existing = (
        await db.execute(
            select(m.Skill).where(m.Skill.tenant_id == principal.tenant_id, m.Skill.name == name)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"a skill named {name!r} already exists — publish a new version instead",
        )

    definition = _validated(
        _definition(
            slug=_slugify(name),
            semver=body.semver,
            instructions=body.instructions,
            tools=body.tools,
            knowledge=body.knowledge,
            guardrails=body.guardrails,
        )
    )

    skill = m.Skill(
        tenant_id=principal.tenant_id,
        name=name,
        category=body.category or None,
        description=body.description,
        author=principal.subject,
        origin="local",
        trust_level="first_party",
    )
    db.add(skill)
    await db.flush()

    version = m.SkillVersion(
        tenant_id=principal.tenant_id,
        skill_id=skill.id,
        semver=body.semver,
        definition=definition,
        artifact_hash=_artifact_hash(definition),
    )
    db.add(version)
    await db.flush()
    skill.current_version_id = version.id
    await db.flush()
    await db.commit()
    return skill_to_dto(skill, version)


@router.post(
    "/skills/{skill_id}/versions",
    response_model=SkillDTO,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(perm(SKILL, MANAGE)))],
)
async def create_skill_version(
    skill_id: uuid.UUID,
    body: CreateVersionRequest,
    db: DbSession,
    principal: CurrentPrincipal,
) -> SkillDTO:
    skill = await _load_skill(db, skill_id, principal.tenant_id)

    duplicate = (
        await db.execute(
            select(m.SkillVersion).where(
                m.SkillVersion.skill_id == skill.id, m.SkillVersion.semver == body.semver
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"version {body.semver} already exists for this skill"
        )

    definition = _validated(
        _definition(
            slug=_slugify(skill.name),
            semver=body.semver,
            instructions=body.instructions,
            tools=body.tools,
            knowledge=body.knowledge,
            guardrails=body.guardrails,
        )
    )
    version = m.SkillVersion(
        tenant_id=principal.tenant_id,
        skill_id=skill.id,
        semver=body.semver,
        definition=definition,
        artifact_hash=_artifact_hash(definition),
    )
    db.add(version)
    await db.flush()
    # Existing assignments deliberately stay pinned to the version they were
    # made against -- publishing must not silently change what a running agent
    # is doing. Re-assign to adopt the new version.
    skill.current_version_id = version.id
    await db.flush()
    await db.commit()
    return skill_to_dto(skill, version)


class UpdateSkillRequest(CamelModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    instructions: str | None = None


@router.patch(
    "/skills/{skill_id}",
    response_model=SkillDTO,
    dependencies=[Depends(require_permission(perm(SKILL, MANAGE)))],
)
async def update_skill(
    skill_id: uuid.UUID, body: UpdateSkillRequest, db: DbSession, principal: CurrentPrincipal
) -> SkillDTO:
    skill = await _load_skill(db, skill_id, principal.tenant_id)
    if skill.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "skill not found")
    if body.name is not None:
        skill.name = body.name
    if body.description is not None:
        skill.description = body.description
    if body.category is not None:
        skill.category = body.category
    version = (
        await db.get(m.SkillVersion, skill.current_version_id) if skill.current_version_id else None
    )
    if body.instructions is not None and version is not None:
        # jsonb: replaced whole, or SQLAlchemy never notices the mutation.
        definition = dict(version.definition)
        definition["instruction"] = body.instructions
        version.definition = definition
    await db.flush()
    await db.commit()
    return skill_to_dto(skill, version)


@router.delete(
    "/skills/{skill_id}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission(perm(SKILL, MANAGE)))],
)
async def delete_skill(
    skill_id: uuid.UUID, db: DbSession, principal: CurrentPrincipal
) -> dict[str, str]:
    """Hard-delete when nothing depends on the skill; archive (soft-delete)
    otherwise -- an agent's assignment must keep pointing at a real row."""
    skill = await _load_skill(db, skill_id, principal.tenant_id)
    if skill.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "skill not found")
    dependents = (
        await db.execute(
            select(func.count())
            .select_from(m.SkillAssignment)
            .join(m.SkillVersion, m.SkillVersion.id == m.SkillAssignment.skill_version_id)
            .where(
                m.SkillVersion.skill_id == skill_id,
                m.SkillAssignment.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    now = dt.datetime.now(tz=dt.UTC)
    if dependents == 0:
        await db.delete(skill)
        outcome = "deleted"
    else:
        skill.deleted_at = now
        outcome = "archived"
    await db.flush()
    await db.commit()
    return {"outcome": outcome}


@router.post(
    "/skills/{skill_id}/restore",
    response_model=SkillDTO,
    dependencies=[Depends(require_permission(perm(SKILL, MANAGE)))],
)
async def restore_skill(
    skill_id: uuid.UUID, db: DbSession, principal: CurrentPrincipal
) -> SkillDTO:
    skill = await _load_skill(db, skill_id, principal.tenant_id)
    if skill.deleted_at is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no archived skill with that id")
    skill.deleted_at = None
    version = (
        await db.get(m.SkillVersion, skill.current_version_id) if skill.current_version_id else None
    )
    await db.flush()
    await db.commit()
    return skill_to_dto(skill, version)


async def _agent_or_404(db: DbSession, agent_id: uuid.UUID, tenant_id: uuid.UUID) -> m.Agent:
    agent = await db.get(m.Agent, agent_id)
    if agent is None or agent.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
    return agent


@router.get(
    "/agents/{agent_id}/skills",
    response_model=list[SkillAssignmentDTO],
    dependencies=[Depends(require_permission(perm(SKILL, VIEW)))],
)
async def list_agent_skills(
    agent_id: uuid.UUID,
    db: DbSession,
    principal: CurrentPrincipal,
) -> list[SkillAssignmentDTO]:
    await _agent_or_404(db, agent_id, principal.tenant_id)
    rows = (
        await db.execute(
            select(m.SkillAssignment, m.SkillVersion, m.Skill)
            .join(m.SkillVersion, m.SkillVersion.id == m.SkillAssignment.skill_version_id)
            .join(m.Skill, m.Skill.id == m.SkillVersion.skill_id)
            .where(
                m.SkillAssignment.agent_id == agent_id,
                m.SkillAssignment.tenant_id == principal.tenant_id,
            )
            .order_by(m.SkillAssignment.created_at)
        )
    ).all()
    return [
        SkillAssignmentDTO(
            id=str(assignment.id),
            agent_id=str(assignment.agent_id),
            skill_id=str(skill.id),
            skill_name=skill.name,
            skill_version_id=str(version.id),
            semver=version.semver,
            enabled=assignment.enabled,
        )
        for assignment, version, skill in rows
    ]


@router.delete(
    "/agents/{agent_id}/skills/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(perm(SKILL, MANAGE)))],
)
async def unassign_skill(
    agent_id: uuid.UUID,
    assignment_id: uuid.UUID,
    db: DbSession,
    principal: CurrentPrincipal,
) -> Response:
    await _agent_or_404(db, agent_id, principal.tenant_id)
    assignment = await db.get(m.SkillAssignment, assignment_id)
    if (
        assignment is None
        or assignment.tenant_id != principal.tenant_id
        or assignment.agent_id != agent_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assignment not found")
    await db.delete(assignment)
    await db.flush()
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------- import


class ImportPreviewRequest(CamelModel):
    source: str
    department_id: uuid.UUID | None = None
    #: Judge against THIS instead of the department's own budget. For the case
    #: the derived number cannot cover: a model whose window nobody has recorded,
    #: or an operator who knows a particular skill is worth the room.
    budget_tokens: int | None = None


class ImportRequest(CamelModel):
    source: str
    names: list[str]
    department_id: uuid.UUID | None = None
    budget_tokens: int | None = None
    #: Import a skill the preview judged too big for the department's model.
    #: Off by default, and named so that saying yes is a decision rather than
    #: an oversight.
    accept_oversized: bool = False


async def _budget_tokens(
    db: DbSession,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID | None,
    override: int | None = None,
) -> int:
    """What one skill may cost in THIS department.

    A skill's instruction arrives as a tool result and stays in the transcript
    for the rest of the run, so its cost is paid against the model's window --
    which is why the same text is unremarkable behind a large model and fatal
    behind a small one. The window is not something the endpoint we speak to
    publishes, so an operator records it on the model config.

    Absent that there is NO budget -- 0 -- and nothing is refused for its size.
    A default would be a number oc8 made up, and it would quietly become the
    rule everyone works around.
    """
    if override is not None and override > 0:
        return override
    if department_id is None:
        return DEFAULT_BUDGET_TOKENS
    agent = (
        await db.execute(
            select(m.Agent)
            .where(m.Agent.tenant_id == tenant_id, m.Agent.department_id == department_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if agent is None or agent.model_config_id is None:
        return DEFAULT_BUDGET_TOKENS
    config = await db.get(m.ModelConfig, agent.model_config_id)
    window = (config.params or {}).get("context_window") if config is not None else None
    try:
        window = int(window)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_BUDGET_TOKENS
    # A quarter of the window: the mission, the tool schemas and the run's own
    # growing transcript all have to fit beside it.
    return max(200, window // 4)


@router.post(
    "/skills/import/preview", dependencies=[Depends(require_permission(perm(SKILL, MANAGE)))]
)
async def preview_import(
    body: ImportPreviewRequest,
    db: DbSession,
    principal: CurrentPrincipal,
) -> dict[str, Any]:
    """What a source offers, and whether it fits here. Imports nothing.

    A skill is an instruction an agent will follow, so importing one from a
    public repository is taking instructions from a stranger. This returns the
    TEXT, not just the names, because a preview an operator cannot read is not
    a preview.
    """
    budget = await _budget_tokens(db, principal.tenant_id, body.department_id, body.budget_tokens)
    try:
        found = await discover(body.source, budget_tokens=budget)
    except ConnectorError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {
        "source": body.source,
        "budgetTokens": budget,
        "skills": [c.to_json() for c in found],
    }


@router.post(
    "/skills/import",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(perm(SKILL, MANAGE)))],
)
async def import_skills(
    body: ImportRequest,
    db: DbSession,
    principal: CurrentPrincipal,
) -> dict[str, Any]:
    """Import exactly the skills named, and say what was skipped and why."""
    budget = await _budget_tokens(db, principal.tenant_id, body.department_id, body.budget_tokens)
    try:
        found = await discover(body.source, budget_tokens=budget)
    except ConnectorError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    wanted = {n.strip() for n in body.names if n.strip()}
    by_name = {c.name: c for c in found}
    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for name in sorted(wanted):
        candidate = by_name.get(name)
        if candidate is None:
            skipped.append({"name": name, "reason": "not found at this source"})
            continue
        if candidate.verdict == "too_big" and not body.accept_oversized:
            skipped.append(
                {
                    "name": name,
                    "reason": (
                        f"{candidate.tokens} tokens against a budget of {budget} — "
                        "it would crowd out the work it is meant to help with"
                    ),
                }
            )
            continue
        existing = (
            await db.execute(
                select(m.Skill).where(
                    m.Skill.tenant_id == principal.tenant_id, m.Skill.name == name
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            skipped.append({"name": name, "reason": "a skill of that name already exists"})
            continue

        version_id = uuid7()
        reference_root = f"imported:{version_id}" if candidate.bundled_files else None
        definition = _validated(
            _definition(
                slug=_slugify(name),
                semver="1.0.0",
                instructions=candidate.instruction,
                tools=[],
                knowledge=[],
                # The warnings travel WITH the skill. An agent that reads "this
                # was written for an agent with a shell" is better placed than
                # one that silently follows an instruction it cannot carry out.
                guardrails=candidate.warnings,
                reference_root=reference_root,
            )
        )
        skill = m.Skill(
            tenant_id=principal.tenant_id,
            name=name,
            category="imported",
            description=candidate.description,
            author=body.source,
            # "store" rather than "local": it came from a catalogue elsewhere,
            # exactly like a skill a plugin ships. Where it came from is in
            # `author`, and that it is not ours is in `trust_level`.
            origin="store",
            # Never first_party: this came from somewhere else, and the
            # catalogue should say so wherever it is shown.
            trust_level="community",
        )
        db.add(skill)
        await db.flush()
        version = m.SkillVersion(
            id=version_id,
            tenant_id=principal.tenant_id,
            skill_id=skill.id,
            semver="1.0.0",
            definition=definition,
            artifact_hash=_artifact_hash(definition),
        )
        db.add(version)
        await db.flush()
        for rel_path, content in candidate.bundled_files.items():
            db.add(
                m.ImportedSkillFile(
                    tenant_id=principal.tenant_id,
                    skill_version_id=version_id,
                    rel_path=rel_path,
                    content=content,
                )
            )
        skill.current_version_id = version.id
        await db.flush()
        imported.append({"id": str(skill.id), "name": name, "tokens": candidate.tokens})

    await db.commit()
    return {"imported": imported, "skipped": skipped, "budgetTokens": budget}
