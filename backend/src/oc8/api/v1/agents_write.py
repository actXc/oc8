"""Agent lifecycle and configuration writes. Every write enforces the PDP where
relevant, is gated on department-scoped agent-write authority (`require_agent_
write` / `authorize_agent_write`, `api/deps.py`), and emits an audit event.

**The ordering rule every route here obeys**: `authorize_agent_write` runs
immediately after the target department is known -- straight after `_load_agent`
(or, for `create_agent`, straight after the department existence check) -- and
strictly BEFORE any frame-derived computation (`narrowing_within_frame`,
`missing_skill_requirements`). Calling it late would let a wrong-department
toggle holder reconstruct that department's tool frame one crafted request's
422 at a time before the refusal ever fires; see `api/deps.py::
authorize_agent_write`'s docstring for the full reasoning. Do not reorder these
calls without re-reading it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, status
from sqlalchemy import func, select

from oc8 import models as m
from oc8.agents.hire import create_hire_request, require_hire_approval
from oc8.api.deps import DbSession, authorize_agent_write, require_agent_write
from oc8.api.v1._serializers import agent_to_dto
from oc8.api.v1.agents import _agent_detail_dto
from oc8.api.v1.files import _attachment_dto, _store_upload
from oc8.audit import append_event
from oc8.authz.pdp import ToolPolicy, missing_skill_requirements, narrowing_within_frame
from oc8.authz.scope import HumanActor
from oc8.modelrouter.subscription_guard import (
    SubscriptionModelNotManualOnly,
    assert_manual_only_compatible,
)
from oc8.runtime.registry import (
    RuntimeCapabilityError,
    RuntimeNotExecutableError,
    RuntimeNotFoundError,
    assign_runtime,
    check_runtime_capabilities,
    resolve_runtime_plugin,
)
from oc8.schemas.dto import AgentDetailDTO, AgentDTO, FileAttachmentDTO
from oc8.schemas.requests import (
    AssignSkillRequest,
    CreateAgentRequest,
    InstructionsRequest,
    LifecycleRequest,
    ModelConfigRequest,
    NarrowingRequest,
    RuntimeAssignRequest,
)

router = APIRouter()


def _violation_body(violations: list[Any]) -> dict[str, Any]:
    return {
        "error": "runtime_capability_violation",
        "missing": [
            {"kind": v.kind, "missingCapability": v.missing_capability, "reason": v.reason}
            for v in violations
        ],
    }


_LIFECYCLE = {"start": "running", "pause": "paused", "stop": "stopped"}


async def _load_agent(db: DbSession, agent_id: uuid.UUID) -> m.Agent:
    agent = await db.get(m.Agent, agent_id)
    if agent is None or agent.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
    return agent


async def _enforce_narrowing_logins(
    db: DbSession, *, tenant_id: uuid.UUID, agent: m.Agent, narrowing: dict[str, Any]
) -> None:
    """Shared by `create_agent` and `set_narrowing`. A tool key can name a
    Credential-backed login (an `McpConnection` row with `credential_id` set
    -- Task 3's `POST /mcp/logins`, tenant-global, no manifest lookup
    involved). Enabling that key without saying which login to use would
    leave the runtime to guess; require the choice explicit instead -- no
    silent fallback (agent tool login selection design's global constraint).
    A key with no such row -- every existing, non-login tool -- is untouched.

    Also records provenance in `narrowing_overridden_keys`: every tool key
    the caller explicitly submitted in THIS request is a deliberate choice,
    by construction -- append-only union, never removed here. This is the
    ONLY writer of `narrowing_overridden_keys` anywhere in the codebase;
    `set_department_tools`'s cascade (departments.py) reads it but must
    never add to it -- that asymmetry is what makes "has this agent
    explicitly overridden this tool" unambiguous, instead of having to infer
    it from `narrowing["tools"][key]`'s mere presence/value, which the
    cascade also writes into and is provably not a reliable signal on its
    own (agent tool login selection design, Task 5 fix round 2).
    """
    raw_tools = narrowing.get("tools", {}) if isinstance(narrowing, dict) else {}
    if isinstance(raw_tools, dict):
        for key, raw in raw_tools.items():
            policy = ToolPolicy.from_json(raw if isinstance(raw, dict) else None)
            if not policy.enabled:
                continue
            login_conn = (
                await db.execute(
                    select(m.McpConnection).where(
                        m.McpConnection.tenant_id == tenant_id,
                        m.McpConnection.name == key,
                        m.McpConnection.credential_id.is_not(None),
                    )
                )
            ).scalar_one_or_none()
            if login_conn is not None and not policy.connection_id:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"tool {key!r} needs a login: set connection_id before enabling it",
                )
    if isinstance(raw_tools, dict) and raw_tools:
        overridden = set(agent.narrowing_overridden_keys or [])
        overridden.update(raw_tools.keys())
        agent.narrowing_overridden_keys = sorted(overridden)


@router.post(
    "/agents",
    response_model=AgentDetailDTO,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent(
    body: CreateAgentRequest,
    db: DbSession,
    request: Request,
    actor: Annotated[HumanActor, Depends(require_agent_write())],
) -> AgentDetailDTO:
    dept = await db.get(m.Department, body.department_id)
    if dept is None or dept.deleted_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown department")

    # The department is now known. Authorize BEFORE `narrowing_within_frame` --
    # its 422 carries `dept.frame`, one violation at a time, and a caller
    # refused only after that call ran could reconstruct a department she has
    # no write authority in from the shape of the refusal alone.
    await authorize_agent_write(
        request,
        db,
        actor,
        dept.id,
        not_found=HTTPException(status.HTTP_400_BAD_REQUEST, "unknown department"),
    )

    if body.narrowing:
        violations = narrowing_within_frame(dept.frame, body.narrowing)
        if violations:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {
                    "error": "narrowing_exceeds_frame",
                    "violations": [v.__dict__ for v in violations],
                },
            )

    principal = actor.principal
    gated = await require_hire_approval(db, tenant_id=principal.tenant_id)
    agent = m.Agent(
        id=uuid.uuid4(),
        tenant_id=principal.tenant_id,
        department_id=body.department_id,
        name=body.name,
        role_title=body.role_title,
        mission=body.mission,
        model_config_id=body.model_config_id,
        narrowing=body.narrowing,
        is_team_lead=body.is_team_lead,
        status="pending_approval" if gated else "stopped",
        trust_level="first_party",
        definition={"oc8_agent": 1, "name": body.name, "mission": body.mission},
        presentation=body.presentation,
    )
    if body.narrowing:
        await _enforce_narrowing_logins(
            db, tenant_id=principal.tenant_id, agent=agent, narrowing=body.narrowing
        )
    db.add(agent)
    db.add(m.MemoryStore(tenant_id=principal.tenant_id, tier="agent", owner_id=agent.id))
    await db.flush()

    # Only when the caller actually named a runtime. Calling the helper with None
    # here would clear a ref that was never set and audit it as `runtime.cleared`
    # -- a deliberate-looking operator action, on every single hire, that nobody
    # performed. `PUT .../runtime` with an explicit null still goes through the
    # helper, because there the clear IS the operator's request.
    if body.runtime_plugin_id is not None:
        try:
            await assign_runtime(
                db,
                tenant_id=principal.tenant_id,
                agent=agent,
                runtime_ref=body.runtime_plugin_id,
                principal=principal,
            )
        except RuntimeNotFoundError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "runtime not found or not enabled"
            ) from exc
        except RuntimeNotExecutableError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "runtime not executable") from exc
        except RuntimeCapabilityError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, _violation_body(exc.violations)
            ) from exc

    if gated:
        await create_hire_request(db, agent=agent)
    await append_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="operator",
        actor_id=None,
        category="admin",
        action="agent.hire_requested" if gated else "agent.created",
        resource={"agent_id": str(agent.id), "name": agent.name, "by": principal.subject},
        principal=principal,
    )
    return await _agent_detail_dto(db, agent)


@router.post(
    "/agents/{agent_id}/lifecycle",
    response_model=AgentDTO,
)
async def lifecycle(
    agent_id: uuid.UUID,
    body: LifecycleRequest,
    db: DbSession,
    request: Request,
    actor: Annotated[HumanActor, Depends(require_agent_write())],
) -> AgentDTO:
    if body.action not in _LIFECYCLE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown lifecycle action")
    agent = await _load_agent(db, agent_id)
    await authorize_agent_write(
        request,
        db,
        actor,
        agent.department_id,
        not_found=HTTPException(status.HTTP_404_NOT_FOUND, "agent not found"),
    )
    principal = actor.principal
    if agent.status == "pending_approval":
        raise HTTPException(status.HTTP_409_CONFLICT, "agent awaiting hire approval")
    agent.status = _LIFECYCLE[body.action]
    await db.flush()
    await append_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="operator",
        actor_id=None,
        category="admin",
        action=f"agent.{body.action}",
        resource={"agent_id": str(agent.id), "status": agent.status, "by": principal.subject},
        principal=principal,
    )
    return agent_to_dto(agent)


@router.put(
    "/agents/{agent_id}/narrowing",
    response_model=AgentDetailDTO,
)
async def set_narrowing(
    agent_id: uuid.UUID,
    body: NarrowingRequest,
    db: DbSession,
    request: Request,
    actor: Annotated[HumanActor, Depends(require_agent_write())],
) -> AgentDetailDTO:
    agent = await _load_agent(db, agent_id)
    await authorize_agent_write(
        request,
        db,
        actor,
        agent.department_id,
        not_found=HTTPException(status.HTTP_404_NOT_FOUND, "agent not found"),
    )
    principal = actor.principal
    dept = await db.get(m.Department, agent.department_id)
    frame = dept.frame if dept else {}
    violations = narrowing_within_frame(frame, body.narrowing)
    if violations:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"error": "narrowing_exceeds_frame", "violations": [v.__dict__ for v in violations]},
        )

    await _enforce_narrowing_logins(
        db, tenant_id=principal.tenant_id, agent=agent, narrowing=body.narrowing
    )
    agent.narrowing = body.narrowing
    await db.flush()
    await append_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="operator",
        actor_id=None,
        category="authz",
        action="agent.narrowing.updated",
        resource={"agent_id": str(agent.id), "by": principal.subject},
        principal=principal,
    )
    return await _agent_detail_dto(db, agent)


@router.put(
    "/agents/{agent_id}/runtime",
    response_model=AgentDetailDTO,
)
async def assign_agent_runtime(
    agent_id: uuid.UUID,
    body: RuntimeAssignRequest,
    db: DbSession,
    request: Request,
    actor: Annotated[HumanActor, Depends(require_agent_write())],
) -> AgentDetailDTO:
    agent = await _load_agent(db, agent_id)
    await authorize_agent_write(
        request,
        db,
        actor,
        agent.department_id,
        not_found=HTTPException(status.HTTP_404_NOT_FOUND, "agent not found"),
    )
    principal = actor.principal

    try:
        await assign_runtime(
            db,
            tenant_id=principal.tenant_id,
            agent=agent,
            runtime_ref=body.runtime_plugin_id,
            principal=principal,
        )
    except RuntimeNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "runtime not found or not enabled") from exc
    except RuntimeNotExecutableError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "runtime not executable") from exc
    except RuntimeCapabilityError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, _violation_body(exc.violations)
        ) from exc

    return await _agent_detail_dto(db, agent)


@router.patch(
    "/agents/{agent_id}/model-config",
    response_model=AgentDTO,
)
async def switch_model(
    agent_id: uuid.UUID,
    body: ModelConfigRequest,
    db: DbSession,
    request: Request,
    actor: Annotated[HumanActor, Depends(require_agent_write())],
) -> AgentDTO:
    agent = await _load_agent(db, agent_id)
    await authorize_agent_write(
        request,
        db,
        actor,
        agent.department_id,
        not_found=HTTPException(status.HTTP_404_NOT_FOUND, "agent not found"),
    )
    principal = actor.principal
    mc = await db.get(m.ModelConfig, body.model_config_id)
    if mc is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown model config")
    # Unlike triggers.py's create/update guard calls (which must run AFTER
    # their own flush, because they check a Trigger row the request itself
    # just created), this check can safely run BEFORE the mutation below:
    # both `agent.id` and `mc.id` already name pre-existing, already-flushed
    # rows -- `mc` was just loaded with `db.get`, and this route never
    # creates a Trigger -- so the guard's own queries (the model's fallback
    # chain, the agent's already-enabled triggers) see identical state
    # whichever side of `agent.model_config_id = mc.id` they run on. That
    # assignment is an in-memory attribute the guard never reads. Verified
    # empirically: swapping the two lines produced the same four pass/fail
    # results (Task 11 report).
    try:
        await assert_manual_only_compatible(db, agent_id=agent.id, model_config_id=mc.id)
    except SubscriptionModelNotManualOnly as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    agent.model_config_id = mc.id
    pres = dict(agent.presentation or {})
    pres["llm"] = mc.display_name or mc.model
    pres["provider"] = mc.provider
    agent.presentation = pres
    # jsonb: replaced whole, or SQLAlchemy never notices the mutation. Each
    # of the four sampling overrides is independent -- a save that doesn't
    # mention a field (not in model_fields_set) leaves whatever this agent
    # already had for it untouched, matching catalog.py's update_model's own
    # per-field semantics; a field present but null clears it back to
    # "inherit the assigned ModelConfig's own value" (resolve_params).
    definition = dict(agent.definition or {})
    model_params = dict(definition.get("model_params") or {})
    if "temperature" in body.model_fields_set:
        if body.temperature is not None:
            model_params["temperature"] = body.temperature
        else:
            model_params.pop("temperature", None)
    if "max_tokens" in body.model_fields_set:
        if body.max_tokens is not None:
            model_params["max_tokens"] = body.max_tokens
        else:
            model_params.pop("max_tokens", None)
    if "effort" in body.model_fields_set:
        if body.effort and body.effort.strip():
            model_params["effort"] = body.effort.strip()
        else:
            model_params.pop("effort", None)
    if "extra" in body.model_fields_set:
        if body.extra:
            model_params["extra"] = body.extra
        else:
            model_params.pop("extra", None)
    if model_params:
        definition["model_params"] = model_params
    else:
        definition.pop("model_params", None)
    agent.definition = definition
    await db.flush()
    await append_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="operator",
        actor_id=None,
        category="admin",
        action="agent.model.switched",
        resource={"agent_id": str(agent.id), "model": mc.model, "by": principal.subject},
        principal=principal,
    )
    return agent_to_dto(agent)


@router.patch(
    "/agents/{agent_id}/instructions",
    response_model=AgentDetailDTO,
)
async def update_instructions(
    agent_id: uuid.UUID,
    body: InstructionsRequest,
    db: DbSession,
    request: Request,
    actor: Annotated[HumanActor, Depends(require_agent_write())],
) -> AgentDetailDTO:
    agent = await _load_agent(db, agent_id)
    await authorize_agent_write(
        request,
        db,
        actor,
        agent.department_id,
        not_found=HTTPException(status.HTTP_404_NOT_FOUND, "agent not found"),
    )
    principal = actor.principal
    before = agent.mission
    agent.mission = body.instructions
    await db.flush()
    # No-op edits (before == after, e.g. Save clicked without changing
    # anything) still get a revision row -- paperclip's own bundle history
    # does the same, and a caller relying on "one Save = one entry" would
    # otherwise have to special-case it.
    await append_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="operator",
        actor_id=None,
        category="admin",
        action="agent.instructions.updated",
        resource={
            "agent_id": str(agent.id),
            "before": before,
            "after": agent.mission,
            "by": principal.subject,
        },
        principal=principal,
    )
    return await _agent_detail_dto(db, agent)


@router.post(
    "/agents/{agent_id}/instruction-files",
    response_model=FileAttachmentDTO,
    status_code=status.HTTP_201_CREATED,
)
async def upload_instruction_file(
    agent_id: uuid.UUID,
    db: DbSession,
    request: Request,
    actor: Annotated[HumanActor, Depends(require_agent_write())],
    file: UploadFile,
) -> FileAttachmentDTO:
    agent = await _load_agent(db, agent_id)
    await authorize_agent_write(
        request,
        db,
        actor,
        agent.department_id,
        not_found=HTTPException(status.HTTP_404_NOT_FOUND, "agent not found"),
    )
    row = await _store_upload(
        db,
        tenant_id=actor.principal.tenant_id,
        owner_type="agent_instructions",
        owner_id=agent.id,
        file=file,
    )
    await db.commit()
    return _attachment_dto(row)


# `list_instruction_files` (GET /agents/{agent_id}/instruction-files) lives in
# `api/v1/agents.py`, not here: it's a read, and this file's own door
# (`require_agent_write`) is for `agent:manage`-parity mutations only. See
# that module's docstring, and `_owned_attachment` in `files.py`, which
# applies the identical view-level check to the SAME owner type
# ("agent_instructions") for GET/DELETE /files/{id} -- listing must not be
# more restrictive than downloading or deleting an individual file by id.


@router.delete(
    "/agents/{agent_id}/memory/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_agent_memory(
    agent_id: uuid.UUID,
    record_id: uuid.UUID,
    db: DbSession,
    request: Request,
    actor: Annotated[HumanActor, Depends(require_agent_write())],
) -> Response:
    agent = await _load_agent(db, agent_id)
    await authorize_agent_write(
        request,
        db,
        actor,
        agent.department_id,
        not_found=HTTPException(status.HTTP_404_NOT_FOUND, "agent not found"),
    )
    principal = actor.principal
    # Scoped by store_id, not just tenant_id -- a record id that exists but
    # belongs to a DIFFERENT agent's (or the department's) store must 404
    # here the same way a foreign agent_id does, not silently delete across
    # owners just because RLS already narrowed the query to this tenant.
    store = (
        await db.execute(
            select(m.MemoryStore).where(
                m.MemoryStore.tenant_id == principal.tenant_id,
                m.MemoryStore.tier == "agent",
                m.MemoryStore.owner_id == agent_id,
            )
        )
    ).scalar_one_or_none()
    record = (
        None
        if store is None
        else (
            await db.execute(
                select(m.MemoryRecord).where(
                    m.MemoryRecord.id == record_id, m.MemoryRecord.store_id == store.id
                )
            )
        ).scalar_one_or_none()
    )
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "memory record not found")
    content_snippet = record.content[:200]
    await db.delete(record)
    await db.flush()
    await append_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="operator",
        actor_id=None,
        category="admin",
        action="agent.memory.deleted",
        resource={
            "agent_id": str(agent_id),
            "record_id": str(record_id),
            "content_snippet": content_snippet,
            "by": principal.subject,
        },
        principal=principal,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/agents/{agent_id}/skills",
    status_code=status.HTTP_201_CREATED,
)
async def assign_skill(
    agent_id: uuid.UUID,
    body: AssignSkillRequest,
    db: DbSession,
    request: Request,
    actor: Annotated[HumanActor, Depends(require_agent_write())],
) -> dict[str, str]:
    agent = await _load_agent(db, agent_id)
    await authorize_agent_write(
        request,
        db,
        actor,
        agent.department_id,
        not_found=HTTPException(status.HTTP_404_NOT_FOUND, "agent not found"),
    )
    principal = actor.principal
    version = await db.get(m.SkillVersion, body.skill_version_id)
    if version is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown skill version")
    dept = await db.get(m.Department, agent.department_id)
    frame = dept.frame if dept else {}
    requires = version.definition.get("requires", {})

    granted = await db.execute(
        select(m.KnowledgeGrant.kb_id).where(
            m.KnowledgeGrant.grantee_id.in_([agent.id, agent.department_id])
        )
    )
    granted_kb_ids = {str(k) for k in granted.scalars().all()}

    missing = missing_skill_requirements(frame, agent.narrowing, requires, granted_kb_ids)
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"error": "requires_exceed_effective", "missing": [x.__dict__ for x in missing]},
        )

    resolved = await resolve_runtime_plugin(db, tenant_id=principal.tenant_id, agent=agent)
    if resolved is not None:
        _, runtime_version = resolved
        violations = check_runtime_capabilities(
            has_supervision=False,
            has_enabled_skills=True,
            runtime_capabilities=list(runtime_version.capabilities),
        )
        if violations:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, _violation_body(violations))

    # Already assigned is not an error worth a 500. The unique index reported the
    # duplicate faithfully and the exception went straight out as an Internal
    # Server Error, which tells an operator that oc8 broke rather than that
    # nothing needed doing.
    existing = (
        await db.execute(
            select(m.SkillAssignment).where(
                m.SkillAssignment.tenant_id == principal.tenant_id,
                m.SkillAssignment.agent_id == agent.id,
                m.SkillAssignment.skill_version_id == version.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if not existing.enabled:
            existing.enabled = True
            # `flush`, not `commit`: a commit inside `tenant_session` unbinds
            # `app.tenant_id` for every statement issued afterward on this same
            # session, and RLS does not raise for that -- it silently returns
            # zero rows. `get_db` commits once, when the request finishes, same
            # as every other write in this file -- the route itself never does.
            await db.flush()
        # Same shape as the fresh path, different status: a caller that says
        # "assigned" when nothing changed teaches the operator to distrust it.
        return {"status": "already_assigned"}

    db.add(
        m.SkillAssignment(
            tenant_id=principal.tenant_id,
            agent_id=agent.id,
            skill_version_id=version.id,
            enabled=True,
        )
    )
    await db.flush()
    await append_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="operator",
        actor_id=None,
        category="admin",
        action="skill.assigned",
        resource={"agent_id": str(agent.id), "skill_version_id": str(version.id)},
        principal=principal,
    )
    return {"status": "assigned"}


@router.delete(
    "/agents/{agent_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_agent(
    agent_id: uuid.UUID,
    db: DbSession,
    request: Request,
    actor: Annotated[HumanActor, Depends(require_agent_write())],
) -> dict[str, str]:
    """Hard-delete when nothing depends on the agent; archive (soft-delete)
    otherwise -- a run's `agent_id` must keep pointing at a real row (same
    dependents-detection shape as `skills_write.py::delete_skill`)."""
    agent = await _load_agent(db, agent_id)
    await authorize_agent_write(
        request,
        db,
        actor,
        agent.department_id,
        not_found=HTTPException(status.HTTP_404_NOT_FOUND, "agent not found"),
    )
    principal = actor.principal
    dependents = (
        await db.execute(
            select(func.count()).select_from(m.AgentRun).where(m.AgentRun.agent_id == agent_id)
        )
    ).scalar_one()
    now = dt.datetime.now(tz=dt.UTC)
    if dependents == 0:
        await db.delete(agent)
        outcome = "deleted"
    else:
        agent.deleted_at = now
        outcome = "archived"
    # `flush`, not `commit`: same reasoning as `assign_skill` above -- the
    # route body never commits itself, `get_db`/`tenant_session` commits once
    # when the request finishes, and a commit here would unbind `app.tenant_id`
    # for nothing this route still needs to read.
    await db.flush()
    await append_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="operator",
        actor_id=None,
        category="admin",
        action=f"agent.{outcome}",
        resource={"agent_id": str(agent_id), "by": principal.subject},
        principal=principal,
    )
    return {"outcome": outcome}


@router.post(
    "/agents/{agent_id}/restore",
    response_model=AgentDTO,
)
async def restore_agent(
    agent_id: uuid.UUID,
    db: DbSession,
    request: Request,
    actor: Annotated[HumanActor, Depends(require_agent_write())],
) -> AgentDTO:
    agent = await db.get(m.Agent, agent_id)
    if agent is None or agent.deleted_at is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no archived agent with that id")
    await authorize_agent_write(
        request,
        db,
        actor,
        agent.department_id,
        not_found=HTTPException(status.HTTP_404_NOT_FOUND, "no archived agent with that id"),
    )
    # Restoring is the moment an agent's triggers start firing again, so it is
    # the moment the manual-trigger-only rule starts applying to it again.
    # `subscription_guard` counts archived agents on both of its directions
    # (see `assert_credential_bind_safe`), so no route should be able to leave
    # an archived agent in this state -- this is the backstop for a pairing
    # that reached the database some other way (seed, plugin, direct SQL, a
    # future write path), checked BEFORE `deleted_at` is cleared so a refusal
    # leaves the agent archived rather than live-and-unattended. `agent` is
    # already loaded, so the common case costs one chain lookup that returns
    # before any trigger row is read.
    try:
        await assert_manual_only_compatible(
            db, agent_id=agent.id, model_config_id=agent.model_config_id
        )
    except SubscriptionModelNotManualOnly as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    principal = actor.principal
    agent.deleted_at = None
    await db.flush()
    await append_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="operator",
        actor_id=None,
        category="admin",
        action="agent.restored",
        resource={"agent_id": str(agent.id), "by": principal.subject},
        principal=principal,
    )
    # `agent_to_dto` is a pure in-memory serialization of the already-loaded
    # `agent` row (presentation/status/etc.) -- no DB read of its own, so
    # building the DTO here (never after a `db.commit()`, which this route
    # never issues) carries none of the "tenant GUC dies at commit" risk
    # `restore_skill` had to work around.
    return agent_to_dto(agent)
