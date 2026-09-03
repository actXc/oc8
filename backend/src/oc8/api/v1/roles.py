"""Composing a role, and handing it to somebody (§6).

Five rungs of ladder exist in code -- `org_admin` (52 of 52), `dept_manager`
(29), `operator` (20), `auditor` (19) and `member` (0) -- and every tenant works
differently. A company that wants somebody who may sign off offers and answer
questions and read nothing else has to choose between handing them twenty
permissions or none. These routes are the sixth rung and every rung after it,
written by the tenant's own IT admin, in one screen, effective on the holder's
next request.

**What a tenant role may be is bounded by set arithmetic, not by a caption.**
`DELEGATABLE_PERMISSIONS` is 21 of the 52; the other 31 are refused here with
the reason rendered next to the box that is disabled. Every `:manage` permission
is among the refused, because `require_permission` structurally cannot carry a
resource -- so a tenant-defined `department:manage` would be tenant-wide
`department:manage`, which is the hole the seat design was written to close.

**The two authorities are separate on purpose.** `role:manage` composes a role;
`member:manage` hands one out. `role:view` merely reads, and is deliberately
wider -- it sweeps into `_VIEW_EVERYTHING`, so an `auditor` can answer "who may
do what" without being able to change it, which is that role's entire job.

`GET /permissions/catalogue` carries no gate at all, for the same reason
`GET /governance` carries none and for the reason 12c40e6 records: the caller who
most needs to read what a right MEANS is the one who was just refused it. It
returns the compiled-in catalogue and no tenant data, so there is nothing to
protect and a gate would only hide the explanation from the person reading the
refusal.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from oc8 import models as m
from oc8.api.deps import CurrentPrincipal, DbSession, require_permission, unguarded
from oc8.audit import append_event
from oc8.auth import Principal
from oc8.authz.authority import Authority, authority_for_principal
from oc8.authz.catalog import catalogue
from oc8.authz.permissions import MANAGE, MEMBER, ROLE, VIEW, perm
from oc8.roles.service import (
    RoleRefused,
    RoleSummary,
    assign_role,
    bounded_by_the_caller,
    create_role,
    delete_role,
    grants_of,
    holder_count_of,
    holders_of,
    list_roles,
    load_role,
    update_role,
    validated_name,
    validated_permissions,
)
from oc8.schemas.dto import (
    PermissionInfoDTO,
    RoleDetailDTO,
    RoleHolderDTO,
    RoleSummaryDTO,
)
from oc8.schemas.requests import (
    BulkAssignRoleRequest,
    CreateRoleRequest,
    SetMemberRoleRequest,
    UpdateRoleRequest,
)
from oc8.workspace.members import get_member

router = APIRouter()

ROLE_VIEW = perm(ROLE, VIEW)
ROLE_MANAGE = perm(ROLE, MANAGE)
MEMBER_MANAGE = perm(MEMBER, MANAGE)


def _refused(exc: RoleRefused) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _summary_dto(summary: RoleSummary) -> RoleSummaryDTO:
    return RoleSummaryDTO(
        id=str(summary.id) if summary.id is not None else None,
        name=summary.name,
        description=summary.description,
        kind=summary.kind,
        builtin=summary.builtin,
        can_edit=summary.can_edit,
        holder_count=summary.holder_count,
        permissions=sorted(summary.permissions),
    )


def _holder_dto(member: m.OrgMember) -> RoleHolderDTO:
    return RoleHolderDTO(
        member_id=str(member.id),
        subject=member.subject,
        display_name=member.display_name,
    )


#: How many holders travel on the wire with a role. The COUNT is exact and the
#: LIST is a preview, because the two answer different questions: "how many people
#: does this change" has to be right, and "which of them" is a set of chips a
#: person reads. `Mitarbeiter` on the five-hundred-person tenant is one role held
#: by four hundred people, and serialising four hundred names into the response of
#: every `POST /roles`, `PUT /roles/{id}`, `DELETE /roles/{id}` and
#: `PUT /members/{id}/role` -- which is what this DTO is the response model of --
#: is an O(n) body on four O(1) writes, rendered as four hundred chips.
HOLDER_PREVIEW: int = 50


async def _detail_dto(
    db: DbSession, role: m.Role, *, holders: list[m.OrgMember] | None = None
) -> RoleDetailDTO:
    """The role, its grants and its holders -- the blast radius before an edit.

    `holders` is passed in by the one caller that has already loaded them for its
    audit event, so an edit reads the holder list once rather than twice.
    """
    if holders is None:
        holders = await holders_of(
            db, tenant_id=role.tenant_id, role_id=role.id, limit=HOLDER_PREVIEW
        )
    return RoleDetailDTO(
        id=str(role.id),
        name=role.name,
        description=role.description,
        kind=role.kind,
        builtin=role.builtin,
        can_edit=not role.builtin,
        holder_count=await holder_count_of(db, tenant_id=role.tenant_id, role_id=role.id),
        permissions=sorted(await grants_of(db, role)),
        holders=[_holder_dto(h) for h in holders[:HOLDER_PREVIEW]],
    )


async def _caller(request: Request, db: DbSession, principal: Principal) -> Authority:
    """The caller's own resolved authority.

    Free: `require_permission` has already resolved it on this request and
    memoised it on `request.state`, so this is the same object the gate compared
    against -- which is the point. A subset rule that asked a DIFFERENT source
    what the caller holds than the gate did would be a rule about a caller who
    does not exist.
    """
    return await authority_for_principal(request, db, principal)


async def _load(
    db: DbSession, principal: Principal, role_id: uuid.UUID, *, for_update: bool = False
) -> m.Role:
    """One live role, optionally locked for the write that is about to follow.

    `for_update=True` on every route that WRITES this row, and on no route that
    reads it. See `load_role`: without the lock, `PUT /roles/{id}`'s
    read-DELETE-INSERT interleaves with a concurrent one into a union, a lost
    revocation, or a 500 -- all three reproduced -- and the audit event records a
    diff that did not happen.
    """
    role = await load_role(
        db, tenant_id=principal.tenant_id, role_id=role_id, for_update=for_update
    )
    if role is None:
        # 404 and not 403, deliberately: RLS means another tenant's role is not
        # merely forbidden, it is not there. Answering 403 would confirm that the
        # id exists somewhere, which is a different fact about another company.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "role not found")
    return role


async def _audit(
    db: DbSession,
    principal: Principal,
    authority: Authority,
    *,
    action: str,
    resource: dict[str, object],
    reason: str | None = None,
) -> None:
    await append_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="operator",
        # The administrator's own member row when this tenant has one for them.
        # `None` rather than a refusal: attribution must not become a
        # prerequisite for the act it describes, and `append_event` still names
        # them through `principal`.
        actor_id=authority.member.id if authority.member is not None else None,
        # The category is the action's own prefix rather than a constant: an
        # assignment is a fact about a PERSON and belongs beside the seat grants
        # `api/v1/members.py` writes, while composing a role is a fact about the
        # tenant's model. Filed under one category, "who changed Anna's
        # authority" would return every role edit in the tenant as well.
        category=action.split(".", 1)[0],
        action=action,
        resource=resource,
        reason=reason,
        principal=principal,
    )


# ------------------------------------------------------------------- catalogue
#
# Declared before the `/roles/{role_id}` routes below. It does not collide with
# them -- it hangs off `/permissions` -- but a future `GET /roles/catalogue`
# would, and FastAPI resolves by declaration order, so a path segment that
# happens to look like a uuid parameter is a class of bug worth keeping the
# habit against.


@router.get(
    "/permissions/catalogue",
    response_model=list[PermissionInfoDTO],
    dependencies=[
        Depends(
            unguarded(
                "explains which rights exist and what each one does, in words; no "
                "operator PERMISSION gates it, because it would hide the catalogue "
                "from the caller composing a role and from the caller reading the "
                "refusal it explains -- but it is AUTHENTICATED like every other "
                "route here, and it returns the compiled-in model with no tenant "
                "data in it"
            )
        )
    ],
)
async def permission_catalogue(principal: CurrentPrincipal) -> list[PermissionInfoDTO]:
    """All 52 permissions, each with a label, a sentence, and its refusal.

    No `db` and no tenant: this is the compiled-in catalogue. It takes a
    `principal` it does not read, and that is the whole point of the parameter --
    resolving `CurrentPrincipal` is what makes `HTTPBearer` run, so an anonymous
    caller gets 401 here as at every other route under `/api/v1`.

    Without it this route answered 200 to a request carrying no `Authorization`
    header at all: `unguarded()` has always meant "no operator PERMISSION", never
    "no authentication", and `GET /governance` -- the precedent this route's
    docstring cited -- takes a `CurrentPrincipal` for exactly this reason. The
    body is no tenant's data, but it is this product's entire authority model
    written out with the refusal prose attached, and *"integration:manage writes
    config.command/args/secret_env verbatim: arbitrary execution"* reads
    differently to an anonymous reader than to the administrator it was written
    for. The frontend only ever calls it from behind a login, so the parameter
    costs nothing.

    The screen renders `permission.split(":")[1]` without it, which turns
    `supervision:manage`, `handoff:manage`, `contract:manage` and `flow:manage`
    into four different rights that all read as "manage" to the person deciding
    whether to tick them.
    """
    del principal
    return [
        PermissionInfoDTO(
            permission=info.permission,
            label=info.label,
            description=info.description,
            label_de=info.label_de,
            description_de=info.description_de,
            delegatable=info.delegatable,
            reason=info.reason,
        )
        for info in catalogue()
    ]


# ------------------------------------------------------------------------ roles


@router.get(
    "/roles",
    response_model=list[RoleSummaryDTO],
    dependencies=[Depends(require_permission(ROLE_VIEW))],
)
async def list_roles_route(db: DbSession, principal: CurrentPrincipal) -> list[RoleSummaryDTO]:
    """The built-in ladder and this tenant's own roles, with their holder counts.

    Behind `role:view` rather than open, and that is what keeps
    `GET /governance`'s `unguarded()` reason literally true: the caller's own
    authority is explained there to everybody, and the tenant's authority MAP --
    which roles exist, who holds them -- lives here, behind a permission. A
    seatless employee can find out why their screen is empty without being able
    to read who in the company may release money.
    """
    return [_summary_dto(s) for s in await list_roles(db, tenant_id=principal.tenant_id)]


@router.post(
    "/roles",
    response_model=RoleDetailDTO,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(ROLE_MANAGE))],
)
async def create_role_route(
    body: CreateRoleRequest,
    request: Request,
    db: DbSession,
    principal: CurrentPrincipal,
) -> RoleDetailDTO:
    """Compose a role out of the offerable rights.

    Validated in this order, and the order is the contract: the PERMISSIONS
    first, so a request that ticks `plugin:manage` is answered with that
    permission and its reason rather than with a complaint about the name it
    happens to be carrying.

    `basedOn` is accepted and dropped. It pre-ticks the boxes in the UI; storing
    it would make it a parent, and a parent is inheritance -- at which point
    editing `operator` in code silently changes every role forked from it, with
    no audit event naming the change and nobody able to see it on the screen.
    """
    try:
        permissions = validated_permissions(body.permissions)
        name = validated_name(body.name)
        authority = await _caller(request, db, principal)
        bounded_by_the_caller(
            authority.tenant_wide, before=frozenset(), after=permissions, what="create"
        )
        role = await create_role(
            db,
            tenant_id=principal.tenant_id,
            name=name,
            description=body.description,
            permissions=permissions,
            created_by=authority.member.id if authority.member is not None else None,
        )
    except RoleRefused as exc:
        raise _refused(exc) from exc

    await _audit(
        db,
        principal,
        authority,
        action="role.created",
        resource={
            "role_id": str(role.id),
            "name": role.name,
            "permissions": sorted(permissions),
        },
        reason=f"created with {len(permissions)} permission(s)",
    )
    dto = await _detail_dto(db, role)
    await db.commit()
    return dto


@router.get(
    "/roles/{role_id}",
    response_model=RoleDetailDTO,
    dependencies=[Depends(require_permission(ROLE_VIEW))],
)
async def get_role_route(
    role_id: uuid.UUID, db: DbSession, principal: CurrentPrincipal
) -> RoleDetailDTO:
    """One role and everybody holding it.

    The holders are the answer to the only question that matters before an edit:
    "how many people does this change, and which of them". A role screen that
    shows a set of checkboxes and no names asks an administrator to click Save on
    a number he cannot see.
    """
    return await _detail_dto(db, await _load(db, principal, role_id))


@router.put(
    "/roles/{role_id}",
    response_model=RoleDetailDTO,
    dependencies=[Depends(require_permission(ROLE_MANAGE))],
)
async def update_role_route(
    role_id: uuid.UUID,
    body: UpdateRoleRequest,
    request: Request,
    db: DbSession,
    principal: CurrentPrincipal,
) -> RoleDetailDTO:
    """Replace what a role grants. Effective on every holder's next request.

    This is the O(1) that is the whole reason roles exist rather than per-person
    grants: *"jede Teamleitung darf ab jetzt auch das Prüfprotokoll lesen"* is
    ONE call that lands for forty people with the token they are already
    carrying. No refresh, no TTL, no cache to invalidate -- which is also why the
    removal direction is safe to offer, and the removal direction is the one that
    matters. A revocation that lands in five minutes is not a revocation.

    The whole write is one transaction committed once, at the end. A commit
    between the DELETE and the INSERTs would unbind `app.tenant_id` and the
    second half would silently affect zero rows.

    **The role row is LOCKED before its grants are read.** Without that, the
    read-DELETE-INSERT of two concurrent edits interleaves, and all three
    outcomes were reproduced against Postgres 15: a revocation answered 200 and
    revoked nothing, two disjoint edits merged into a union, and two overlapping
    edits raised a bare unique-violation 500. In the first two the audit event
    below states a diff that never happened, because `before` was read from a
    snapshot the other transaction had already overwritten. A revocation that
    lands in five minutes is not a revocation, and a revocation that answers 200
    and lands never is worse.
    """
    role = await _load(db, principal, role_id, for_update=True)
    try:
        permissions = validated_permissions(body.permissions)
        authority = await _caller(request, db, principal)
        bounded_by_the_caller(
            authority.tenant_wide,
            before=await grants_of(db, role),
            after=permissions,
            what="edit",
        )
        added, removed = await update_role(
            db, role=role, description=body.description, permissions=permissions
        )
    except RoleRefused as exc:
        raise _refused(exc) from exc

    # Read ONCE, and handed to `_detail_dto` below rather than read again for it.
    holders = await holders_of(db, tenant_id=role.tenant_id, role_id=role.id, limit=HOLDER_PREVIEW)
    holder_total = await holder_count_of(db, tenant_id=role.tenant_id, role_id=role.id)
    await _audit(
        db,
        principal,
        authority,
        action="role.updated",
        resource={
            "role_id": str(role.id),
            "name": role.name,
            # The DIFF, not the resulting set. "role.updated" with only the new
            # value records that something changed and refuses to say what --
            # and this is the entry somebody reads a year later asking when a
            # team lead stopped being able to decide.
            "added": sorted(added),
            "removed": sorted(removed),
            "holder_count": holder_total,
        },
        reason=f"+{len(added)} / -{len(removed)} for {holder_total} holder(s)",
    )
    dto = await _detail_dto(db, role, holders=holders)
    await db.commit()
    return dto


@router.delete(
    "/roles/{role_id}",
    response_model=RoleDetailDTO,
    dependencies=[Depends(require_permission(ROLE_MANAGE))],
)
async def delete_role_route(
    role_id: uuid.UUID,
    request: Request,
    db: DbSession,
    principal: CurrentPrincipal,
    reassign_to: Annotated[
        uuid.UUID | None,
        Query(
            alias="reassignTo",
            description="move every holder to this role instead of refusing the delete",
        ),
    ] = None,
) -> RoleDetailDTO:
    """Soft-delete a role, refusing while somebody holds it.

    409 naming the holders, unless `?reassignTo=` says where they go. The
    alternative -- deleting it and letting the holders fall back -- restores
    every one of them to whatever their TOKEN says, which on every live tenant is
    `org_admin`. A cleanup would silently re-promote forty people, and nothing
    would look wrong anywhere.

    `?reassignTo=` is a GRANT, so it is bounded by the caller's own set exactly
    as the body of a PUT is. Without that term,
    `DELETE /roles/{weak}?reassignTo={strong}` moves forty people to a stronger
    role while the caller never names a single permission. It is also an
    ASSIGNMENT, so it carries the self-demotion refusal `PUT /members/{id}/role`
    carries -- a caller who holds the role being deleted is one of the people
    `?reassignTo=` moves, and moving himself somewhere without `member:manage` is
    the same lockout by a different verb.

    The role being deleted is LOCKED; the target is not. Two administrators each
    deleting a role while naming the other's as the target would otherwise take
    two row locks in opposite orders, and a deadlock is a worse defect than the
    stale read it would close.
    """
    role = await _load(db, principal, role_id, for_update=True)
    target = None if reassign_to is None else await _load(db, principal, reassign_to)
    try:
        authority = await _caller(request, db, principal)
        bounded_by_the_caller(
            authority.tenant_wide,
            before=await grants_of(db, role),
            after=frozenset() if target is None else await grants_of(db, target),
            what="delete",
        )
        moved = await delete_role(
            db, role=role, reassign_to=target, caller_subject=principal.subject
        )
    except RoleRefused as exc:
        raise _refused(exc) from exc

    await _audit(
        db,
        principal,
        authority,
        action="role.deleted",
        resource={
            "role_id": str(role.id),
            "name": role.name,
            "reassigned_to": None if target is None else str(target.id),
            "moved": [str(h.id) for h in moved],
        },
        # `moved` non-empty implies a target: a held role with no `?reassignTo=`
        # is refused above and never reaches here.
        reason=(
            f"deleted; {len(moved)} holder(s) moved to {target.name!r}"
            if moved and target is not None
            else "deleted"
        ),
    )
    dto = await _detail_dto(db, role)
    await db.commit()
    return dto


# ------------------------------------------------------------------ assignment


async def _member_for(
    db: DbSession, principal: Principal, member_id: str, subject: str | None
) -> m.OrgMember:
    """The person this assignment is about, by id or by `?subject=`.

    `?subject=` exists so a CSV of token subjects can drive the route without the
    caller having to resolve forty ids first -- the subject is the one identifier
    an HR export and a JWT agree on. It RESOLVES ONLY: a subject this tenant has
    never seen is a 404 pointing at `POST /members`, because minting a person as
    a side effect of assigning them a role would make a typo in a spreadsheet
    into a permanent row nobody enrolled.
    """
    if subject is not None:
        by_subject = (
            (
                await db.execute(
                    select(m.OrgMember).where(
                        m.OrgMember.tenant_id == principal.tenant_id,
                        m.OrgMember.subject == subject,
                        m.OrgMember.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .first()
        )
        if by_subject is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"this tenant knows no member with subject {subject!r}; enrol "
                "them with POST /members first",
            )
        return by_subject

    try:
        parsed = uuid.UUID(member_id)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{member_id!r} is not a member id; pass a uuid, or ?subject=<token sub>",
        ) from exc
    found = await get_member(db, tenant_id=principal.tenant_id, member_id=parsed)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    return found


async def _assign(
    db: DbSession,
    request: Request,
    principal: Principal,
    member: m.OrgMember,
    role: m.Role | None,
    grants: dict[uuid.UUID, frozenset[str]] | None = None,
) -> Authority:
    """One assignment, bounded and audited. The caller commits.

    `grants` memoises "what does role X grant" for the length of ONE request.
    Forty people onboarded onto one role asked that question about the same role
    forty times; it is loop-invariant, and on the five-hundred-person tenant the
    loop is the onboarding.
    """
    if grants is None:
        grants = {}

    async def _granted(of: m.Role) -> frozenset[str]:
        if of.id not in grants:
            grants[of.id] = await grants_of(db, of)
        return grants[of.id]

    authority = await _caller(request, db, principal)
    before_role = (
        None
        if member.role_id is None
        else await load_role(db, tenant_id=principal.tenant_id, role_id=member.role_id)
    )
    bounded_by_the_caller(
        authority.tenant_wide,
        before=frozenset() if before_role is None else await _granted(before_role),
        after=frozenset() if role is None else await _granted(role),
        what="assign",
    )
    await assign_role(
        db,
        member=member,
        role=role,
        caller_subject=principal.subject,
        role_grants=None if role is None else await _granted(role),
    )
    await _audit(
        db,
        principal,
        authority,
        action="member.role_assigned" if role is not None else "member.role_cleared",
        resource={
            "member_id": str(member.id),
            "subject": member.subject,
            "role_id": None if role is None else str(role.id),
            "role": None if role is None else role.name,
            "previous_role_id": None if before_role is None else str(before_role.id),
        },
        reason=(
            f"assigned {role.name!r}"
            if role is not None
            # Said explicitly, because "cleared" reads like a removal of
            # authority and is usually the opposite: the person goes back to
            # whatever their token carries, which today is `org_admin`.
            else "cleared the assignment; the token's role decides again"
        ),
    )
    return authority


@router.put(
    "/members/{member_id}/role",
    response_model=RoleDetailDTO | None,
    dependencies=[Depends(require_permission(MEMBER_MANAGE))],
)
async def set_member_role(
    member_id: str,
    body: SetMemberRoleRequest,
    request: Request,
    db: DbSession,
    principal: CurrentPrincipal,
    subject: str | None = None,
) -> RoleDetailDTO | None:
    """Give one person a role, or take the override away.

    Effective on their NEXT REQUEST, with the token they are already holding.
    That is the property that lets authority over money stay out of the JWT, and
    it is why neither this route nor its inverse talks to the identity provider:
    the joiner flow is one system for everything except the password.

    `{"roleId": null}` restores the token floor. It is not "no permissions" and
    it is the documented repair for a demotion.
    """
    member = await _member_for(db, principal, member_id, subject)
    role = (
        None
        if body.role_id is None
        else await load_role(db, tenant_id=principal.tenant_id, role_id=body.role_id)
    )
    if body.role_id is not None and role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "role not found")
    try:
        await _assign(db, request, principal, member, role)
    except RoleRefused as exc:
        raise _refused(exc) from exc
    dto = None if role is None else await _detail_dto(db, role)
    await db.commit()
    return dto


@router.post(
    "/members/roles:bulk",
    response_model=list[RoleHolderDTO],
    dependencies=[Depends(require_permission(MEMBER_MANAGE))],
)
async def bulk_assign_role(
    body: BulkAssignRoleRequest,
    request: Request,
    db: DbSession,
    principal: CurrentPrincipal,
) -> list[RoleHolderDTO]:
    """The same role for many people, in one transaction, one audit event each.

    ONE transaction because a commit per person inside `tenant_session` unbinds
    `app.tenant_id`, and every assignment after the first would then match no RLS
    policy and affect zero rows -- returning 200 with forty names on it and one
    row written. (The CSV importer in the CLI is the mirror image of this and
    opens a transaction PER ROW for the same reason: it must not lose 499 people
    because row 500 named a role that does not exist.)

    One audit event per assignment, not one for the batch: "forty people were
    given a role" is not an answer to "when did Anna get this, and who did it".
    """
    role = (
        None
        if body.role_id is None
        else await load_role(db, tenant_id=principal.tenant_id, role_id=body.role_id)
    )
    if body.role_id is not None and role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "role not found")

    assigned: list[m.OrgMember] = []
    # One memo for the whole batch: the target role's grants are the same answer
    # for every member, and `_assign` would otherwise re-read them per person.
    grants: dict[uuid.UUID, frozenset[str]] = {}
    for member_id in dict.fromkeys(body.member_ids):
        member = await get_member(db, tenant_id=principal.tenant_id, member_id=member_id)
        if member is None:
            # Refused as a whole rather than partially applied. A bulk write that
            # silently skips the ids it did not recognise is a write whose result
            # nobody can state: the caller is told 200 and has no way to know
            # which of his forty rows landed.
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"member not found: {member_id}")
        try:
            await _assign(db, request, principal, member, role, grants)
        except RoleRefused as exc:
            raise _refused(exc) from exc
        assigned.append(member)

    dtos = [_holder_dto(member) for member in assigned]
    await db.commit()
    return dtos
