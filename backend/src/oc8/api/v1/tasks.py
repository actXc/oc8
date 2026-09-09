"""The My Work task board: what is being worked on across every department a
member can see, and the one new door to put something on it directly.

Everything real lives in `workspace/tasks.py`; this file is only the two
routes on top of it, gated the same way `chat.py`'s `post_message` gates
starting a run against a departmental agent -- `AGENT_VIEW` departmentally
gets you in the door, `RUN_START` tenant-wide is what lets you actually start
one. A member who can see a department's board but holds no `run:start` can
look, not act, which is the same split the board's approvals and
clarifications columns already draw.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from oc8.api.deps import DbSession, require_departmental
from oc8.api.v1._serializers import task_row_to_dto
from oc8.authz.authority import authority_for_principal
from oc8.authz.permissions import AGENT, RUN_START, VIEW, perm
from oc8.authz.scope import HumanActor
from oc8.schemas.dto import TaskBoardRowDTO
from oc8.schemas.requests import CreateTaskRequest
from oc8.workspace.tasks import DEFAULT_LIMIT, NoTeamLead, create_task, visible_tasks

router = APIRouter()


@router.get("/tasks", response_model=list[TaskBoardRowDTO])
async def list_tasks(
    db: DbSession,
    actor: Annotated[HumanActor, Depends(require_departmental(perm(AGENT, VIEW)))],
    department_id: uuid.UUID | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[TaskBoardRowDTO]:
    rows = await visible_tasks(db, actor=actor, department_id=department_id, limit=limit)
    return [task_row_to_dto(r) for r in rows]


@router.post("/tasks", response_model=TaskBoardRowDTO)
async def create(
    body: CreateTaskRequest,
    request: Request,
    db: DbSession,
    actor: Annotated[HumanActor, Depends(require_departmental(perm(AGENT, VIEW)))],
) -> TaskBoardRowDTO:
    authority = await authority_for_principal(request, db, actor.principal)
    if RUN_START not in authority.tenant_wide:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"requires permission: {RUN_START}"
        )
    try:
        # `create_task` commits and publishes internally (it funnels through
        # `enqueue_run`, the same "commit, then publish" single intake point
        # every run source uses) -- nothing further to do here.
        row = await create_task(
            db,
            actor=actor,
            tenant_id=actor.principal.tenant_id,
            department_id=uuid.UUID(body.department_id),
            member_id=actor.member.id,
            instructions=body.instructions,
            title=body.title,
        )
    except NoTeamLead as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return task_row_to_dto(row)
