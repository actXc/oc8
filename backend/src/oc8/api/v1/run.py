"""Trigger an agent run (durable): create an AgentRun, enqueue it for the worker,
and return its queued status. Poll GET /runs/{id} for progress."""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.api.deps import CurrentPrincipal, DbSession, require_permission
from oc8.audit import append_event
from oc8.authz.permissions import RUN, RUN_CONTROL, RUN_START, VIEW, perm
from oc8.config import get_settings
from oc8.runtime.clarification import resolve_clarification
from oc8.runtime.intake import enqueue_run, publish_run
from oc8.runtime.registry import RuntimeResolutionError, resolve_runtime_plugin
from oc8.runtime.states import TERMINAL, RunState
from oc8.schemas.dto import RunDTO, WorkspaceFileContentDTO, WorkspaceFileDTO, WorkspaceFilesDTO
from oc8.schemas.requests import RunAgentRequest

router = APIRouter()

#: The only runtime plugins that leave a per-run HOST workspace directory
#: behind (mounted at /workspace in their container -- see each plugin's
#: runtime.py `_workspace_dir`, always exactly
#: f"{settings.runtime_session_root}/{run_id}"). The in-process "Standard"
#: runtime and the isolated-shell runtime (oc8.runtime.isolated) write no
#: such directory, so an agent on either has nothing for this endpoint to
#: list.
_WORKSPACE_RUNTIME_PLUGINS = {"opencode_runtime", "codex_runtime", "claude_code_runtime"}

#: Evidence states whose run still has a readable directory on disk. "present"
#: is the ordinary case; "none" is what the evidence sweep (oc8.evidence.sweep)
#: sets for these three runtimes specifically, since none of them implement
#: EvidenceProducingRuntime -- the sweep never touches their directory, it
#: just marks the run as having nothing FOR IT to archive. "archived" and
#: "reduced" mean the loose directory is gone (packed into an archive file or
#: deleted outright), which cannot currently happen to these three runtimes'
#: workspaces but is checked anyway rather than assumed.
_WORKSPACE_READABLE_EVIDENCE_STATES = {"present", "none"}

#: A safety bound on a single file read, mirroring docker_driver.py's
#: DEFAULT_EXEC_TIMEOUT-adjacent logs() cap in spirit: this endpoint must not
#: let one huge file in a run's workspace pin a request's memory/response
#: size indefinitely.
_MAX_WORKSPACE_FILE_BYTES = 2_000_000


async def _workspace_runtime_plugin_name(
    db: AsyncSession, *, tenant_id: uuid.UUID, agent: m.Agent
) -> str | None:
    """The runtime plugin's manifest name if `agent` runs on one, else None.

    `resolve_runtime_plugin` raises when `agent.runtime_ref` names a plugin
    that is no longer installed/enabled -- swallowed here into "no workspace",
    since a broken runtime assignment has nothing this endpoint can list
    either way, and the run/agent endpoints already surface that breakage
    elsewhere.
    """
    try:
        resolved = await resolve_runtime_plugin(db, tenant_id=tenant_id, agent=agent)
    except RuntimeResolutionError:
        return None
    if resolved is None:
        return None
    plugin, _version = resolved
    return plugin.name


def _workspace_root(run_id: uuid.UUID) -> str:
    return os.path.join(get_settings().runtime_session_root, str(run_id))


def _list_workspace_files(root: str) -> list[WorkspaceFileDTO]:
    if not os.path.isdir(root):
        return []
    out: list[WorkspaceFileDTO] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            out.append(WorkspaceFileDTO(name=name, path=rel, size=size))
    out.sort(key=lambda f: f.path)
    return out


def _resolve_workspace_path(root: str, rel_path: str) -> str | None:
    """`root`-relative `rel_path` resolved to a real path, or None if it
    contains a `..` segment or resolves outside `root` -- the path-traversal
    gate every caller of this must pass through before touching the disk."""
    if ".." in rel_path.split("/"):
        return None
    root_real = os.path.realpath(root)
    candidate_real = os.path.realpath(os.path.join(root, rel_path))
    if candidate_real != root_real and not candidate_real.startswith(root_real + os.sep):
        return None
    return candidate_real


def _read_workspace_file(root: str, rel_path: str) -> bytes | None:
    """Resolve `rel_path` safely under `root` and return its raw bytes (one
    more than `_MAX_WORKSPACE_FILE_BYTES`, so the caller can tell whether the
    real file is bigger than what was read), or None if it is outside the
    root, doesn't exist, or isn't a regular file. A plain sync function so it
    can run as a single unit inside `asyncio.to_thread` -- see its callers."""
    resolved = _resolve_workspace_path(root, rel_path)
    if resolved is None or not os.path.isfile(resolved):
        return None
    with open(resolved, "rb") as fh:
        return fh.read(_MAX_WORKSPACE_FILE_BYTES + 1)


async def _most_recent_run(db: AsyncSession, *, agent_id: uuid.UUID) -> m.AgentRun | None:
    # `id` (uuid7, time-ordered) is the tiebreaker: `created_at` is a
    # server-side `now()` default, which Postgres resolves to the SAME value
    # for every row written inside one transaction -- two runs enqueued back
    # to back can and do tie on it, and ORDER BY alone on a tied column is
    # non-deterministic. uuid7 stays strictly increasing even for rows that
    # share a transaction, so it is what actually decides "most recent" here.
    return (
        await db.execute(
            select(m.AgentRun)
            .where(m.AgentRun.agent_id == agent_id)
            .order_by(m.AgentRun.created_at.desc(), m.AgentRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


class AnswerRequest(BaseModel):
    answer: str


class RunMessageRequest(BaseModel):
    body: str


def run_to_dto(run: m.AgentRun) -> RunDTO:
    ctx = run.context or {}
    return RunDTO(
        id=str(run.id),
        agent_id=str(run.agent_id),
        state=run.state,
        phase=run.phase,
        output=ctx.get("output"),
        steps=int(ctx.get("steps", 0)),
        tool_calls=ctx.get("toolCalls", []),
        task_id=str(run.task_id) if run.task_id else None,
        question=ctx.get("pending_question"),
        rendered_components=ctx.get("rendered_components", []),
        todos=ctx.get("todos", []),
    )


@router.post(
    "/agents/{agent_id}/run",
    response_model=RunDTO,
    dependencies=[Depends(require_permission(RUN_START))],
)
async def run(
    agent_id: uuid.UUID,
    body: RunAgentRequest,
    db: DbSession,
    principal: CurrentPrincipal,
) -> RunDTO:
    agent = await db.get(m.Agent, agent_id)
    if agent is None or agent.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
    if agent.status == "pending_approval":
        raise HTTPException(status.HTTP_409_CONFLICT, "agent awaiting hire approval")

    context: dict[str, object] = {"task": body.task}
    # Record the originating operator ONLY for an operator principal (§12.5 A3):
    # this route has no role gate, so a plugin/agent token could reach it, and its
    # subject must NOT be resolved as ("operator", subject) in the audit trail --
    # that mirrors resolve_responsible's own kind=="operator" guard on the live
    # principal. A non-operator run falls through to agent/tenant attribution.
    if principal.kind == "operator":
        context["originating_operator"] = principal.subject
    if body.mcp_connection_id is not None:
        context["mcp_connection_id"] = str(body.mcp_connection_id)

    # enqueue_run commits `db` before publishing; run_to_dto(run_row) below only
    # reads already-loaded attributes (safe because the sessionmaker uses
    # expire_on_commit=False).
    run_row, _published = await enqueue_run(
        db, tenant_id=principal.tenant_id, agent_id=agent.id, context=context, source="manual"
    )
    return run_to_dto(run_row)


@router.get(
    "/agents/{agent_id}/workspace/files",
    response_model=WorkspaceFilesDTO,
    dependencies=[Depends(require_permission(perm(RUN, VIEW)))],
)
async def list_workspace_files(
    agent_id: uuid.UUID, db: DbSession, principal: CurrentPrincipal
) -> WorkspaceFilesDTO:
    """Files in the agent's MOST RECENT run's sandbox workspace (§8.1 host
    bind-mount, `{runtime_session_root}/{run_id}`). Only opencode/codex/
    claude_code_runtime write one -- everything else (the in-process
    "Standard" runtime, the isolated-shell runtime) answers `applicable:
    false` rather than an empty list, so the frontend can tell "nothing there
    yet" from "this runtime has nothing to show, ever" apart."""
    agent = await db.get(m.Agent, agent_id)
    if agent is None or agent.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")

    plugin_name = await _workspace_runtime_plugin_name(
        db, tenant_id=principal.tenant_id, agent=agent
    )
    if plugin_name not in _WORKSPACE_RUNTIME_PLUGINS:
        return WorkspaceFilesDTO(
            applicable=False,
            message="This agent's runtime has no file workspace.",
        )

    run_row = await _most_recent_run(db, agent_id=agent_id)
    if run_row is None:
        return WorkspaceFilesDTO(applicable=True, message="No runs yet.")
    if run_row.evidence_state not in _WORKSPACE_READABLE_EVIDENCE_STATES:
        return WorkspaceFilesDTO(
            applicable=True,
            run_id=str(run_row.id),
            message=f"This run's workspace evidence is {run_row.evidence_state}.",
        )

    # Off the event loop: os.walk over a workspace directory is blocking I/O,
    # same reasoning as docker_driver.py wrapping every docker-py call in
    # asyncio.to_thread.
    files = await asyncio.to_thread(_list_workspace_files, _workspace_root(run_row.id))
    return WorkspaceFilesDTO(applicable=True, run_id=str(run_row.id), files=files)


@router.get(
    "/agents/{agent_id}/workspace/files/{file_path:path}",
    response_model=WorkspaceFileContentDTO,
    dependencies=[Depends(require_permission(perm(RUN, VIEW)))],
)
async def get_workspace_file(
    agent_id: uuid.UUID, file_path: str, db: DbSession, principal: CurrentPrincipal
) -> WorkspaceFileContentDTO:
    """One file's content out of the agent's most recent run's workspace.
    `file_path` is relative to the workspace root and is resolved through
    `_resolve_workspace_path`, which rejects any `..` segment and anything
    that resolves outside the workspace root -- the path-traversal gate."""
    agent = await db.get(m.Agent, agent_id)
    if agent is None or agent.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")

    plugin_name = await _workspace_runtime_plugin_name(
        db, tenant_id=principal.tenant_id, agent=agent
    )
    if plugin_name not in _WORKSPACE_RUNTIME_PLUGINS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this agent's runtime has no workspace")

    run_row = await _most_recent_run(db, agent_id=agent_id)
    if run_row is None or run_row.evidence_state not in _WORKSPACE_READABLE_EVIDENCE_STATES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace not available")

    # Off the event loop, and one round-trip: resolve, existence-check and
    # read all happen inside the thread, same reasoning as the listing above.
    raw = await asyncio.to_thread(_read_workspace_file, _workspace_root(run_row.id), file_path)
    if raw is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    truncated = len(raw) > _MAX_WORKSPACE_FILE_BYTES
    content = raw[:_MAX_WORKSPACE_FILE_BYTES].decode("utf-8", errors="replace")
    return WorkspaceFileContentDTO(path=file_path, content=content, truncated=truncated)


@router.get(
    "/runs/{run_id}",
    response_model=RunDTO,
    dependencies=[Depends(require_permission(perm(RUN, VIEW)))],
)
async def get_run(run_id: uuid.UUID, db: DbSession, principal: CurrentPrincipal) -> RunDTO:
    run_row = await db.get(m.AgentRun, run_id)
    if run_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return run_to_dto(run_row)


@router.post(
    "/runs/{run_id}/answer",
    response_model=RunDTO,
    dependencies=[Depends(require_permission(RUN_CONTROL))],
)
async def answer_run(
    run_id: uuid.UUID,
    body: AnswerRequest,
    db: DbSession,
    principal: CurrentPrincipal,
) -> RunDTO:
    run_row = await db.get(m.AgentRun, run_id)
    if run_row is None or run_row.tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    if run_row.state != RunState.WAITING_FOR_INPUT.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "run is not waiting for input")
    # Record the answer, fold it into the run context, and re-queue the
    # already-existing run (not a new one, so publish_run, not enqueue_run).
    await resolve_clarification(db, run=run_row, answer=body.answer)
    # No further DB query after commit (the transaction-local tenant binding
    # ends); run_to_dto only reads already-loaded attributes.
    await db.commit()
    await publish_run(run_id=run_row.id, tenant_id=principal.tenant_id)
    return run_to_dto(run_row)


@router.post(
    "/runs/{run_id}/message",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission(RUN_CONTROL))],
)
async def message_run(
    run_id: uuid.UUID,
    body: RunMessageRequest,
    db: DbSession,
    principal: CurrentPrincipal,
) -> dict[str, str]:
    """Send a message to a RUNNING agent (live steering). Recorded in the
    append-only run_message table; the executor injects it as a user turn at its
    next step boundary (READ COMMITTED), so the agent answers inline without the
    run being suspended. Distinct from /answer, which resolves an ask_user."""
    text = body.body.strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "body must not be empty")
    run_row = await db.get(m.AgentRun, run_id)
    if run_row is None or run_row.tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    if run_row.state in TERMINAL:
        raise HTTPException(status.HTTP_409_CONFLICT, "run has already finished")
    db.add(
        m.RunMessage(
            tenant_id=principal.tenant_id,
            run_id=run_id,
            author=principal.subject or "operator",
            body=text,
        )
    )
    await db.commit()
    return {"status": "queued"}


@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunDTO,
    dependencies=[Depends(require_permission(RUN_CONTROL))],
)
async def cancel_run(
    run_id: uuid.UUID,
    db: DbSession,
    principal: CurrentPrincipal,
) -> RunDTO:
    """Cancel a run cooperatively (§7.2, Paperclip A5). Records the operator's
    intent as a run_cancellation row; the executor (the sole agent_run.state
    mutator) applies it. This is deliberately NOT synchronous, so the returned
    DTO reports the run's CURRENT state, not `interrupted`:

    - queued  -> skipped before start on the worker's next pickup (agent freed then).
    - running -> stopped at the next step boundary, before the next LLM call.
    - waiting_for_input / waiting_for_approval -> a suspended run is not looping,
      so the cancel is recorded and honored only when the run is next resumed
      (via /answer or approval), which re-queues it into the before-start check.
      Until then the run stays suspended and its agent stays occupied. Callers
      must therefore inspect the returned `state` (and poll) rather than assume a
      200 means the run has already stopped. Immediately interrupting a suspended
      run is out of scope for this slice (queued + running); see the design spec's
      Non-Goals.

    409 if the run is already terminal; 404 if missing or cross-tenant (RLS).
    Idempotent: a second cancel adds no duplicate row.
    """
    run_row = await db.get(m.AgentRun, run_id)
    if run_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")

    current = RunState(run_row.state)
    if current in TERMINAL:
        raise HTTPException(status.HTTP_409_CONFLICT, f"run already {current.value}")

    # Record the cancel on an UNLOCKED row. NEVER write agent_run here: a running
    # executor holds that row's lock for the whole run, so a write would block
    # until the run ends. The executor is the sole agent_run.state mutator and
    # honors this row -- skipping a queued run before start, or stopping a running
    # run at its next step boundary. Idempotent: a second cancel adds no row.
    existing = (
        await db.execute(select(m.RunCancellation).where(m.RunCancellation.run_id == run_id))
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            m.RunCancellation(
                tenant_id=principal.tenant_id,
                run_id=run_id,
                requested_at=dt.datetime.now(tz=dt.UTC),
                cancellation_kind="operator_interrupted",
            )
        )

    await append_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="operator",
        actor_id=None,
        category="run",
        action="run.cancel",
        resource={"run_id": str(run_id), "was_state": current.value},
        decision="allow",
        principal=principal,
    )
    await db.commit()
    return run_to_dto(run_row)
