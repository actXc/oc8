"""Who this tenant knows, and where each of them is allowed to stand (§5).

`member:manage` is `org_admin`-only. Deliberately in no seat vocabulary and
deliberately not in `_DEPT_MANAGER`, which otherwise carries nine `:manage`
grants: a Head of Sales who can enrol himself in Engineering is the department
boundary in a different coat, and it would arrive through the very screen this
slice exists to give him.

These four routes use `require_permission`, not `require_departmental`. That is
the point -- no seat grants authority over seats, so there is nothing
departmental to resolve, and a gate that admitted "somebody with a seat
somewhere" would be exactly the hole above.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.api.deps import CurrentPrincipal, DbSession, require_permission
from oc8.api.v1._serializers import member_to_dto
from oc8.audit import append_event
from oc8.auth import Principal
from oc8.auth.password import PasswordHashingError, hash_password
from oc8.authz.permissions import MANAGE, MEMBER, VIEW, perm
from oc8.authz.scope import scope_for_principal, subject_uuid_for
from oc8.schemas.dto import MemberDTO
from oc8.schemas.paging import Page
from oc8.schemas.requests import (
    CreateMemberRequest,
    GrantSeatRequest,
    RenameMemberSubjectRequest,
    SetMemberPasswordRequest,
)
from oc8.workspace.members import (
    MemberRow,
    UnknownSeatRole,
    get_member,
    grant_seat,
    list_members,
    revoke_seat,
    role_names_for,
    seats_for,
    upsert_member,
)

router = APIRouter()


async def _acting_member_id(db: AsyncSession, principal: Principal) -> uuid.UUID | None:
    """The administrator's own `org_member.id`, for `granted_by` and `actor_id`.

    `None` rather than an exception for a principal that cannot stand in a
    department (a plugin token): this is attribution, and refusing the whole act
    because the actor has no row would make an audit column a prerequisite for the
    thing it describes. `append_event` still names them through `principal`.
    """
    try:
        member, _scope = await scope_for_principal(db, principal, upsert=True)
    except PermissionError:
        return None
    return member.id if member is not None else None


async def _member_dto(db: AsyncSession, member: m.OrgMember) -> MemberDTO:
    """The person as the screen sees them, with their live seats re-read.

    Built BEFORE the caller commits, deliberately: a commit inside
    `tenant_session` unbinds `app.tenant_id`, so a seat query issued afterwards
    would come back empty and the response would say the person holds nothing.
    """
    names = await role_names_for(db, tenant_id=member.tenant_id, role_ids=[member.role_id])
    return member_to_dto(
        MemberRow(
            id=member.id,
            subject=member.subject,
            display_name=member.display_name,
            all_departments=member.all_departments,
            first_seen_at=member.created_at,
            seats=tuple(await seats_for(db, member.id)),
            role_id=member.role_id,
            role_name="" if member.role_id is None else names.get(member.role_id, ""),
        )
    )


@router.get(
    "/members",
    response_model=Page[MemberDTO],
    dependencies=[Depends(require_permission(perm(MEMBER, VIEW)))],
)
async def list_members_route(
    db: DbSession,
    principal: CurrentPrincipal,
    search: str | None = None,
    group_by: str | None = Query(None, alias="groupBy"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> Page[MemberDTO]:
    """Everybody the tenant has seen, with their LIVE seats.

    Revoked seats are not listed and are not deleted either -- they keep their
    row so an audit can answer "who could approve this, and until when".

    Widened to the Design System Consistency plan's uniform search/filter/
    group/pagination contract (spec §1.1) -- `Page[MemberDTO]` instead of a
    bare list, matching Tasks 3-8's other list-query routes. No
    `includeArchived`: `OrgMember` has no `SoftDeleteMixin` and archive was
    never requested for Members (per the plan's Global Constraint, Members'
    detail view and CRUD stay out of scope -- only this list door widens).
    """
    rows, total = await list_members(
        db,
        tenant_id=principal.tenant_id,
        search=search,
        group_by=group_by,
        limit=limit,
        offset=offset,
    )
    return Page(items=[member_to_dto(r) for r in rows], total_count=total)


@router.post(
    "/members",
    response_model=MemberDTO,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(perm(MEMBER, MANAGE)))],
)
async def create_member(
    body: CreateMemberRequest,
    db: DbSession,
    principal: CurrentPrincipal,
    response: Response,
) -> MemberDTO:
    """Enrol a person by their token subject, or adopt the row they already have.

    An upsert on `(tenant_id, subject)` and not an insert, because the gate mints
    a row on somebody's first request: an administrator enrolling a colleague who
    signed in this morning would otherwise get a unique-violation 500 for doing
    the obvious thing. The status code says which of the two happened -- 201 for a
    row this request created, 200 for one it merged into -- so a client that
    branches on it is not told a lie.

    `allDepartments` is tri-state and this is the ONLY writer of it in either
    direction: omitted leaves it alone, `true` grants company-wide view and
    decide, `false` takes it back. It only ever widened until now, so an
    administrator revoking it was answered 200 while the flag stayed set -- and
    the flag outlives the role that earned it, so a person demoted elsewhere
    kept the authority to sign off every approval in the company.
    """
    member, created = await upsert_member(
        db,
        tenant_id=principal.tenant_id,
        subject=body.subject,
        display_name=body.display_name,
        all_departments=body.all_departments,
    )
    if body.password is not None:
        try:
            member.password_hash = hash_password(body.password)
        except PasswordHashingError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Could not hash password."
            ) from exc
        await append_event(
            db,
            tenant_id=principal.tenant_id,
            actor_type="operator",
            actor_id=await _acting_member_id(db, principal),
            category="member",
            action="member.password_set",
            resource={"member_id": str(member.id), "subject": member.subject},
            reason="password set through POST /members",
            principal=principal,
        )
    if body.all_departments is not None:
        # Audited on every request that ASSERTS it, in either direction, and not
        # only on the one that changed the value. `all_departments` is the only
        # unrestricted term the messenger door can read -- a Telegram message
        # carries no token -- so asserting it is an act whatever the previous
        # value was, and "when did this stop being true" is the question an audit
        # asks about a revocation.
        granted = body.all_departments
        action = "member.all_departments_granted" if granted else "member.all_departments_revoked"
        await append_event(
            db,
            tenant_id=principal.tenant_id,
            actor_type="operator",
            actor_id=await _acting_member_id(db, principal),
            category="member",
            action=action,
            resource={"member_id": str(member.id), "subject": member.subject},
            reason=("granted" if granted else "revoked") + " through POST /members",
            principal=principal,
        )
    dto = await _member_dto(db, member)
    await db.commit()
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return dto


@router.put(
    "/members/{member_id}/password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(perm(MEMBER, MANAGE)))],
)
async def set_member_password(
    member_id: uuid.UUID,
    body: SetMemberPasswordRequest,
    db: DbSession,
    principal: CurrentPrincipal,
) -> None:
    """Set or reset a member's local password (Argon2id), administrator-only.

    `POST /auth/login` checks `password_hash` alone, so this works in any
    deployment. Never returns the hash or echoes the plaintext: a 204 says
    only that it happened, the way a "password changed" screen should.
    """
    member = await get_member(db, tenant_id=principal.tenant_id, member_id=member_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    try:
        member.password_hash = hash_password(body.password)
    except PasswordHashingError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Could not hash password."
        ) from exc
    await append_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="operator",
        actor_id=await _acting_member_id(db, principal),
        category="member",
        action="member.password_reset",
        resource={"member_id": str(member.id), "subject": member.subject},
        reason="password reset by an administrator",
        principal=principal,
    )
    await db.commit()


@router.put(
    "/members/{member_id}/subject",
    response_model=MemberDTO,
    dependencies=[Depends(require_permission(perm(MEMBER, MANAGE)))],
)
async def rename_member_subject(
    member_id: uuid.UUID,
    body: RenameMemberSubjectRequest,
    db: DbSession,
    principal: CurrentPrincipal,
) -> MemberDTO:
    """Change a member's sign-in identity -- their email, in local-password mode.

    `subject_uuid` is a deterministic function of `subject` (see
    `authz.scope.subject_uuid_for`), so both columns are rewritten together;
    leaving the old `subject_uuid` behind would desync the row from the very
    identity check the rename is supposed to update. Refused with a 409, not
    a silent adoption, if another live member in this tenant already holds
    the target subject -- `(tenant_id, subject)` is unique, and the row that
    lost the race would otherwise look like it simply vanished.
    """
    member = await get_member(db, tenant_id=principal.tenant_id, member_id=member_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")

    new_subject = body.subject.strip()
    if new_subject != member.subject:
        clash = (
            await db.execute(
                select(m.OrgMember.id).where(
                    m.OrgMember.tenant_id == principal.tenant_id,
                    m.OrgMember.subject == new_subject,
                    m.OrgMember.deleted_at.is_(None),
                    m.OrgMember.id != member.id,
                )
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Another user already signs in with that identity."
            )
        old_subject = member.subject
        member.subject = new_subject
        member.subject_uuid = subject_uuid_for(new_subject)
        await append_event(
            db,
            tenant_id=principal.tenant_id,
            actor_type="operator",
            actor_id=await _acting_member_id(db, principal),
            category="member",
            action="member.subject_renamed",
            resource={
                "member_id": str(member.id),
                "previous_subject": old_subject,
                "subject": new_subject,
            },
            reason="sign-in identity changed by an administrator",
            principal=principal,
        )

    dto = await _member_dto(db, member)
    await db.commit()
    return dto


@router.put(
    "/members/{member_id}/departments/{department_id}",
    response_model=MemberDTO,
    dependencies=[Depends(require_permission(perm(MEMBER, MANAGE)))],
)
async def grant_seat_route(
    member_id: uuid.UUID,
    department_id: uuid.UUID,
    body: GrantSeatRequest,
    db: DbSession,
    principal: CurrentPrincipal,
) -> MemberDTO:
    """Seat somebody in one department, at one of exactly two seat roles.

    The department's existence is checked before the seat is written. There is no
    foreign key on `org_member_department.department_id` -- the seat outlives an
    archived department on purpose -- so without this check a typo produces a seat
    in a department that does not exist: invisible on every screen, matching no
    approval, and not diagnosable from the row itself.
    """
    member = await get_member(db, tenant_id=principal.tenant_id, member_id=member_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    department = await db.get(m.Department, department_id)
    if department is None or department.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "department not found")

    granted_by = await _acting_member_id(db, principal)
    try:
        seat, changed = await grant_seat(
            db,
            tenant_id=principal.tenant_id,
            member_id=member.id,
            department_id=department_id,
            seat_role=body.seat_role,
            agent_manage=body.agent_manage,
            granted_by=granted_by,
        )
    except UnknownSeatRole as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    if changed:
        await append_event(
            db,
            tenant_id=principal.tenant_id,
            actor_type="operator",
            actor_id=granted_by,
            category="member",
            action="member.seat_granted",
            resource={
                "member_id": str(member.id),
                "subject": member.subject,
                "department_id": str(department_id),
                "department": department.name,
                "seat_role": seat.seat_role,
                # Always named, not only on a toggle-only change: an admin
                # reading the log should see current state regardless of what
                # changed this row.
                "agent_manage": seat.agent_manage,
            },
            principal=principal,
        )
    dto = await _member_dto(db, member)
    await db.commit()
    return dto


@router.delete(
    "/members/{member_id}/departments/{department_id}",
    response_model=MemberDTO,
    dependencies=[Depends(require_permission(perm(MEMBER, MANAGE)))],
)
async def revoke_seat_route(
    member_id: uuid.UUID,
    department_id: uuid.UUID,
    db: DbSession,
    principal: CurrentPrincipal,
) -> MemberDTO:
    """Take a seat away. Effective on that person's NEXT request.

    No token refresh, no TTL, no logout -- a seat is read from this row on every
    request, which is the whole reason it is not in the JWT. The row is kept and
    gains a `revoked_at`, so "who could approve this, and until when" stays
    answerable afterwards.
    """
    member = await get_member(db, tenant_id=principal.tenant_id, member_id=member_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")

    seat = await revoke_seat(
        db, tenant_id=principal.tenant_id, member_id=member.id, department_id=department_id
    )
    if seat is None:
        # Said out loud rather than answered 204. A revoke that silently succeeds
        # against a seat nobody holds is how an administrator walks away believing
        # they took an authority away that is still held.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no live seat in that department")

    await append_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="operator",
        actor_id=await _acting_member_id(db, principal),
        category="member",
        action="member.seat_revoked",
        resource={
            "member_id": str(member.id),
            "subject": member.subject,
            "department_id": str(department_id),
            "seat_role": seat.seat_role,
            "granted_at": seat.created_at.isoformat() if seat.created_at else None,
        },
        principal=principal,
    )
    dto = await _member_dto(db, member)
    await db.commit()
    return dto
