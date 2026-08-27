"""Dev and local password authentication endpoints.

`dev-login` mints a token for a seeded tenant so the frontend can authenticate
during early development, without going through the real setup/login flow.

Local password authentication (WP-B) provides `setup`, `login`, and `logout` endpoints
for Community single-instance deployments. These operate alongside the existing dev flow --
the only two identity paths this edition ships.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from oc8 import models as m
from oc8.api.deps import CurrentPrincipal, DbSession, unguarded
from oc8.api.v1._serializers import member_to_dto, seat_to_dto
from oc8.auth import Principal, get_identity_provider
from oc8.auth.password import PasswordHashingError, hash_password, verify_password
from oc8.auth.totp_gate import totp_gate
from oc8.authz.permissions import MEMBER_ROLE
from oc8.authz.scope import scope_for_principal
from oc8.config import get_settings
from oc8.constants import ACME_TENANT_ID, DEV_OPERATOR_SUBJECT
from oc8.db.session import tenant_session
from oc8.schemas.base import CamelModel
from oc8.schemas.dto import AuthConfig, MeDTO, MemberDTO
from oc8.tenants.provision import TenantExists, create_tenant, list_tenants
from oc8.workspace.members import list_members, seats_for

router = APIRouter()

_COMMUNITY_INITIAL_SLUG = "oc8-community"
_COMMUNITY_INITIAL_NAME = "OC8 Community"


class DevLoginRequest(CamelModel):
    """A `CamelModel`, and that is the whole fix.

    It was a plain `BaseModel` with a snake_case `tenant_id` while the frontend --
    like every other body in this API -- sends `tenantId`. Pydantic ignored the
    unknown key and fell back to the default, so EVERY dev token was minted for
    ACME whatever was clicked in the tenant picker. That is not cosmetic for a
    department-scoped workspace: a seat granted in another tenant is invisible to
    an ACME token, and the screen shows an empty queue that looks exactly like the
    failure it is meant to be distinguishable from.

    `populate_by_name` is on (`CamelModel`), so the snake_case bodies that already
    exist keep working.
    """

    tenant_id: uuid.UUID = ACME_TENANT_ID
    role: str = "org_admin"
    subject: str = DEV_OPERATOR_SUBJECT


class TokenResponse(BaseModel):
    token: str
    principal: Principal


class DevTenantRequest(BaseModel):
    name: str
    slug: str
    region: str = "eu"


class DevTenantDTO(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    region: str


def _dev_only() -> None:
    if not get_settings().is_dev:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


@asynccontextmanager
async def _owner_session() -> AsyncIterator[AsyncSession]:
    """Dev tooling still writes as schema owner; the app role never gets this power."""
    engine = create_async_engine(get_settings().migration_async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            yield db
    finally:
        await engine.dispose()


@router.post(
    "/auth/dev-login",
    response_model=TokenResponse,
    dependencies=[
        Depends(unguarded("exists to be reachable without a token; PIN-gated and dev-only"))
    ],
)
async def dev_login(body: DevLoginRequest) -> TokenResponse:
    _dev_only()
    provider = get_identity_provider()
    token = provider.mint(tenant_id=body.tenant_id, subject=body.subject, role=body.role)
    principal = provider.verify(token)
    return TokenResponse(token=token, principal=principal)


@router.get(
    "/auth/dev-tenants",
    response_model=list[DevTenantDTO],
    dependencies=[Depends(unguarded("dev login helper, reachable before any token exists"))],
)
async def dev_tenants() -> list[DevTenantDTO]:
    _dev_only()
    async with _owner_session() as db:
        return [
            DevTenantDTO(id=row.tenant_id, name=row.name, slug=row.slug, region=row.region)
            for row in await list_tenants(db)
        ]


@router.get(
    "/auth/dev-members",
    response_model=list[MemberDTO],
    dependencies=[Depends(unguarded("dev login helper, reachable before any token exists"))],
)
async def dev_members(tenant_id: uuid.UUID = ACME_TENANT_ID) -> list[MemberDTO]:
    """Who has already shown up in this tenant, for the sign-in picker.

    `tenant_id` is a plain query parameter, not a `CamelModel` body field --
    FastAPI does not camelCase those on its own, so it stays snake_case on the
    wire (`?tenant_id=...`), unlike every JSON body in this API.
    `test_dev_login_honours_the_requested_tenant.py` exists because the
    equivalent body field silently fell back to its default when the frontend
    sent `tenantId`; a query param has the same trap in the other direction --
    sending `tenantId` here would silently return ACME's list instead.

    `list_members` already batches seats and role names in one pass (see its
    own docstring on why); this is a thin, dev-only, pre-token wrapper around
    it, over the owner session for the same reason `dev_tenants` is -- there is
    no principal yet to bind an RLS session to.

    Logging a listed person back in always mints them the token role `member`,
    never their tenant role: that IS the design (§0.A) -- a human's token holds
    nothing, their authority is seats and an assigned tenant role, read live on
    every request. `dev-login`'s own `role` field stays reachable directly for
    the quick-access buttons that test the built-in ladder without a seat.
    """
    _dev_only()
    async with _owner_session() as db:
        rows, _total = await list_members(db, tenant_id=tenant_id)
        return [member_to_dto(r) for r in rows]


@router.post(
    "/auth/dev-tenants",
    response_model=DevTenantDTO,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(unguarded("dev login helper, reachable before any token exists"))],
)
async def create_dev_tenant(body: DevTenantRequest) -> DevTenantDTO:
    """Local-only convenience around the same owner-session provisioning used by the CLI.

    Community is single-tenant in production: the real onboarding path is
    ``POST /auth/setup`` against the one bootstrapped organization, not this
    dev-only tenant picker.
    """
    _dev_only()
    try:
        async with _owner_session() as db:
            created = await create_tenant(
                db, slug=body.slug, name=body.name, region=body.region, department_name="Sales"
            )
            await db.commit()
            return DevTenantDTO(
                id=created.tenant_id, name=body.name, slug=created.slug, region=body.region
            )
    except TenantExists as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "/me",
    response_model=MeDTO,
    dependencies=[
        Depends(
            unguarded(
                "returns only the caller's own identity and the seats they "
                "themselves hold, which every caller may see"
            )
        )
    ],
)
async def me(principal: CurrentPrincipal, db: DbSession) -> MeDTO:
    """The caller, and where they stand.

    Additive over what this returned before -- `subject`, `role` and `kind` are
    the three fields the frontend reads and none of them moved. What is new is
    the pair the workspace turns on: `seats` and `viewsAllDepartments`.

    Without them the screen cannot tell "nothing is waiting for you" from "nobody
    has put you in a department yet". Those are the same blank page today, and one
    of them is the system working while the other is a person locked out of their
    own job.

    The member row is upserted here, on a GET, and that is deliberate: a person's
    first request is what mints them, which is what keeps
    `approval_request.decided_by` from being NULL and lets `POST /members` offer
    subjects the system has actually seen instead of asking an administrator to
    type an id by hand.
    """
    org = await db.get(m.Organization, principal.tenant_id)
    onboarding_status = org.onboarding_status if org is not None else None
    try:
        member, scope = await scope_for_principal(db, principal, upsert=True)
    except PermissionError:
        # A principal that cannot stand in a department at all (a plugin token;
        # an agent token never reaches the operator API). It still gets its own
        # identity back -- this route is how a caller finds out what it is -- but
        # with no member and no seats rather than a 500.
        return MeDTO(
            subject=principal.subject,
            tenant_id=str(principal.tenant_id),
            role=principal.role,
            kind=principal.kind,
            scopes=list(principal.scopes),
            onboarding_status=onboarding_status,
        )

    # `include_archived=False`: this answers WHERE THIS PERSON STANDS, and
    # `authz/scope.py` stopped counting a seat in an archived department when it
    # started joining `Department`. A seat listed here that grants nothing would
    # put the workspace in the wrong empty state -- "nothing is waiting for you"
    # instead of "nobody has assigned you to a department". `GET /members` still
    # sees it, because that is the screen that has to revoke it.
    seats = await seats_for(db, member.id, include_archived=False) if member is not None else []
    return MeDTO(
        subject=principal.subject,
        tenant_id=str(principal.tenant_id),
        role=principal.role,
        kind=principal.kind,
        scopes=list(principal.scopes),
        display_name=member.display_name if member is not None else "",
        member_id=str(member.id) if member is not None else None,
        # The scope's own answer, not `role == "org_admin"`: an auditor holds
        # `approval:view_any` and an `all_departments` row-CEO holds no role at
        # all, and both of them see every department.
        views_all_departments=scope.is_unrestricted,
        # BOTH flags, because they are two different people. Sending only the
        # first made the screen offer an auditor a button the backend refuses on
        # every click -- the answer was in the scope and simply never left the
        # process.
        decides_all_departments=scope.decides_everywhere,
        seats=[seat_to_dto(s) for s in seats],
        onboarding_status=onboarding_status,
    )


@router.get(
    "/auth/config",
    response_model=AuthConfig,
    dependencies=[
        Depends(unguarded("read before login: tells the browser which identity provider to use"))
    ],
)
async def auth_config() -> AuthConfig:
    """Which identity provider the browser should use, checked before login.

    Two modes: `is_dev` (env == "dev") is the SAME flag that gates
    `dev-login`/`dev-tenants`/`dev-members` behind a 404 in `_dev_only()` --
    so this stays consistent with what those endpoints actually allow. A
    self-hosted deployment with env != "dev" has no dev endpoints: it must
    report "community" so the frontend routes to /login (real password auth)
    instead of falling through to the dev persona picker, which would be
    unreachable there.
    """
    s = get_settings()
    if s.is_dev:
        return AuthConfig(mode="dev", auth_server_url="", realm="", client_id="")
    return AuthConfig(
        mode="community",
        auth_server_url="",
        realm="",
        client_id="",
        initialized=await _instance_has_an_administrator(),
    )


async def _instance_has_an_administrator() -> bool:
    """Whether `POST /auth/setup` would refuse because setup already ran.

    Deliberately the SAME predicate that endpoint uses -- a member count above
    zero on the singleton organization -- so the login page cannot offer a
    setup form that setup itself will reject.

    Unresolvable states (no organization yet, or more than one) answer
    `False`. That routes the browser to the setup form, which is where the
    specific 404/409 explaining the real problem comes from; a login form
    would just fail to authenticate against a database that has no one to
    authenticate.

    No `DbSession` parameter, for the reason spelled out on `/auth/setup`:
    this runs before any token can exist, so the session is opened by hand.
    """
    try:
        async with tenant_session(None) as unbound_db:
            org = await _get_singleton_organization(unbound_db)
        async with tenant_session(org.id) as db:
            return await _count_members_in_org(db, org.id) > 0
    except HTTPException:
        return False


# =============================================================================
# Local Password Authentication (WP-B) — Community Single-Instance Only
# =============================================================================
# These endpoints implement real local password authentication for Community
# self-hosted deployments. They operate alongside the existing dev flow and
# must not break it.
#
# Spec references: oc8_auth_and_multitenancy_split_spec.md §4.1, §8

#: The grace window itself now lives with the decision that reads it, in
#: `oc8.auth.totp_gate.TOTP_GRACE_DAYS` -- a second copy of the deadline
#: here would be a second deadline.


class PasswordSetupRequest(CamelModel):
    """Initial admin setup: create the first member with a password."""

    email: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=1024)
    display_name: str = Field(default="", max_length=255)


class PasswordLoginRequest(CamelModel):
    """Authenticate an existing member via password."""

    email: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=1024)


class PasswordSessionResponse(CamelModel):
    """Response from login/setup: a new session token and caller info."""

    token: str
    principal: Principal
    member_id: str
    #: Standalone 2FA design. Non-null only on a full-session response for a
    #: mandatory org_admin who hasn't enrolled yet; the frontend uses it to
    #: show a grace-period nag.
    totp_grace_expires_at: str | None = None
    #: True when `token` above is a NARROW totp:challenge-scoped token, not
    #: a real session -- the caller must POST /auth/totp/verify next.
    requires_totp_code: bool = False
    #: True when `token` above is a NARROW totp:enroll-scoped token -- the
    #: caller must POST /auth/totp/enroll then /confirm next.
    requires_totp_enrollment: bool = False


class PasswordSessionInfo(CamelModel):
    """Current session info (used by GET /auth/session)."""

    authenticated: bool
    member_id: str | None = None
    email: str | None = None
    display_name: str | None = None


async def _get_singleton_organization(db: AsyncSession) -> m.Organization:
    """Resolve the singleton Organization for Community single-instance.

    Per spec §3.2, Community has exactly one active Organization (root).
    This function enforces that invariant at runtime for password auth.

    Raises:
        HTTPException(404): if no Organization exists (setup not complete)
        HTTPException(409): if multiple Organizations exist (data corruption)
    """
    result = await db.execute(select(m.Organization))
    orgs = result.scalars().all()

    if len(orgs) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Instance not initialized. No Organization found.",
        )

    if len(orgs) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Multi-organization configuration detected. Setup is ambiguous.",
        )

    return orgs[0]


async def _count_members_in_org(db: AsyncSession, org_id: uuid.UUID) -> int:
    """Count non-soft-deleted members in an Organization."""
    result = await db.execute(
        select(func.count(m.OrgMember.id)).where(
            m.OrgMember.tenant_id == org_id,
            m.OrgMember.deleted_at.is_(None),
        )
    )
    return result.scalar() or 0


async def _bootstrap_community_organization_if_empty() -> None:
    """Create Community's one instance root only for a genuinely empty database.

    Migrations intentionally create schema but not customer data.  The first
    unauthenticated setup request is therefore the one permitted place to
    create the singleton root; ``create_tenant`` supplies its required baseline
    roles, department, templates, and audit event.  Existing roots still go
    through the normal fail-closed singleton validation in ``password_setup``.
    """
    async with _owner_session() as db:
        existing = (await db.execute(select(m.Organization.id).limit(2))).scalars().all()
        if existing:
            return
        await create_tenant(
            db,
            slug=_COMMUNITY_INITIAL_SLUG,
            name=_COMMUNITY_INITIAL_NAME,
            department_name="General",
        )
        await db.commit()


@router.post(
    "/auth/setup",
    response_model=PasswordSessionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(unguarded("setup is callable before any token exists; checked by endpoint"))
    ],
)
async def password_setup(body: PasswordSetupRequest) -> PasswordSessionResponse:
    """Initialize a Community/Enterprise instance with a local admin.

    POST /auth/setup is idempotent per-subject but fails if:
    - Any members already exist in the Organization (instance already set up)
    - Organization does not exist or multiple Organizations exist
    - Password is invalid (too short/long)

    This endpoint creates the first org_member and mints an initial JWT token
    bound to the singleton Organization ID. No further setup is required;
    the frontend may proceed to /welcome or redirect to /dashboard.

    Email is stored as-is in org_member.subject (not normalized). The password
    is hashed with Argon2id and stored in org_member.password_hash. The subject
    never appears in logs or error messages.

    Request: email and password (plain, min 8 chars).
    Response: token (JWT), principal (parsed claims), member_id (UUID).

    Raises:
        404: Organization not found (database not yet initialized)
        409: Multiple Organizations detected (data corruption)
        422: member_count > 0 (instance already set up)
        422: Invalid password (too short/long, hashing failed)

    No `db: DbSession` parameter here on purpose: `DbSession` resolves through
    `get_db` -> `get_principal` -> a mandatory Bearer token, which cannot exist
    yet on an empty instance. `unguarded(...)` above only exempts the ROUTE from
    the permission-governance test -- it does nothing to a `DbSession` parameter,
    which still demands a token. Sessions are opened by hand instead: first
    unbound (organization alone is readable unbound, see db/session.py), then
    bound to the resolved singleton tenant for the actual member write.
    """
    # A freshly migrated Community database has no customer rows. Bootstrap its
    # single root before the normal unbound singleton discovery below.
    await _bootstrap_community_organization_if_empty()

    # Resolve the singleton instance. Unbound read: db/session.py grants an
    # unbound session read access to `organization` specifically so this kind
    # of pre-auth bootstrap can find the one tenant to bind to.
    async with tenant_session(None) as unbound_db:
        org = await _get_singleton_organization(unbound_db)

    # Hash the password before opening the write session, so a slow hash never
    # holds the tenant-bound transaction open longer than it has to.
    try:
        password_hash = hash_password(body.password)
    except PasswordHashingError as err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password hashing failed. Try a different password.",
        ) from err

    async with tenant_session(org.id) as db:
        # Verify this is the first setup: 0 members allowed
        member_count = await _count_members_in_org(db, org.id)
        if member_count > 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Instance already initialized. Setup can only be run once.",
            )

        # The minted token below carries `role: "org_admin"`, but that claim
        # is only ever read when `org_member.role_id IS NULL` (authz/authority.py)
        # -- and `password_login` never re-mints "org_admin" into a token (by
        # design, see that function's own comment), only the empty MEMBER_ROLE.
        # Leaving `role_id` unset here used to mean this admin's authority
        # existed nowhere but that one, short-lived token: the next login
        # after it expired silently dropped them to zero permissions, with no
        # other admin left to grant it back. `create_tenant` always seeds a
        # builtin "org_admin" Role per tenant (tenants/provision.py), so it is
        # resolved and persisted here instead of trusted to the JWT alone.
        admin_role_id = (
            await db.execute(
                select(m.Role.id).where(
                    m.Role.tenant_id == org.id,
                    m.Role.name == "org_admin",
                    m.Role.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if admin_role_id is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Tenant provisioning is incomplete: no org_admin role found.",
            )

        # Create the org_member row
        # Use email as the subject (the unique identifier for this user in this instance)
        member = m.OrgMember(
            tenant_id=org.id,
            subject=body.email,  # Email as subject for local auth
            subject_uuid=uuid.uuid5(uuid.NAMESPACE_URL, f"oc8:local:{body.email}"),
            display_name=body.display_name or body.email,
            password_hash=password_hash,
            all_departments=True,  # Initial admin sees all departments
            role_id=admin_role_id,
            totp_grace_started_at=dt.datetime.now(tz=dt.UTC),
        )
        db.add(member)
        await db.flush()
        member_id = member.id

        # The member row above just set `totp_grace_started_at`, so this is
        # always outcome 1 (full_session_ok) -- nothing can be enrolled yet
        # on a member that didn't exist a moment ago. Routed through the
        # shared `totp_gate` anyway rather than recomputing the deadline
        # inline: one copy of the grace-window arithmetic, not two (see that
        # module's own docstring on why a second copy is how this drifts).
        outcome = await totp_gate(db, member_id=member_id)

    # Mint a token for this new member
    provider = get_identity_provider()
    token = provider.mint(
        tenant_id=org.id,
        subject=body.email,
        role="org_admin",  # Initial admin
        kind="operator",
    )

    # Verify the token to return principal info
    principal = provider.verify(token)

    return PasswordSessionResponse(
        token=token,
        principal=principal,
        member_id=str(member_id),
        totp_grace_expires_at=(
            outcome.totp_grace_expires_at.isoformat()
            if outcome.totp_grace_expires_at is not None
            else None
        ),
    )


@router.post(
    "/auth/login",
    response_model=PasswordSessionResponse,
    dependencies=[
        Depends(unguarded("login is pre-auth; all checking is inside the endpoint"))
    ],
)
async def password_login(body: PasswordLoginRequest) -> PasswordSessionResponse:
    """Authenticate a member via email + password.

    POST /auth/login verifies the email and password against the stored
    Argon2id hash in org_member.password_hash. On success, a new JWT token
    is minted and returned.

    Only members with a non-NULL password_hash can use this endpoint (those
    created via /auth/setup). A member with no password_hash set receives a
    401.

    Email lookup is case-sensitive and exact-match. Passwords are compared
    using constant-time verification (Argon2). Failed logins do not disclose
    whether the email exists.

    Request: email and password (plain).
    Response: token (JWT), principal (parsed claims), member_id (UUID).

    Raises:
        404: Organization not found
        401: Email not found, password mismatch, or member has no password_hash

    No `db: DbSession` parameter here either -- see password_setup's docstring
    for why: it would silently demand a Bearer token that cannot exist yet.
    """
    # Resolve the singleton instance (unbound read, see password_setup)
    async with tenant_session(None) as unbound_db:
        org = await _get_singleton_organization(unbound_db)

    # Look up the member by email (exact match, case-sensitive), tenant-bound
    async with tenant_session(org.id) as db:
        result = await db.execute(
            select(m.OrgMember).where(
                m.OrgMember.tenant_id == org.id,
                m.OrgMember.subject == body.email,
                m.OrgMember.deleted_at.is_(None),
            )
        )
        member = result.scalar_one_or_none()
        member_id = member.id if member is not None else None
        password_hash = member.password_hash if member is not None else None

    # Fail closed: wrong email, missing password_hash, or soft-deleted member
    if member_id is None or password_hash is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    # Verify password (constant-time comparison, returns bool not exception)
    if not verify_password(body.password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    # The token's `role` claim only matters when `org_member.role_id IS NULL`
    # (see authz/authority.py's module docstring: an assigned role_id is an
    # OVERRIDE that makes this claim irrelevant). It used to be hardcoded to
    # "org_admin" -- meaning EVERY password-authenticated member with no
    # explicitly assigned role silently held all 52 permissions, not just the
    # ones an admin actually granted them. MEMBER_ROLE resolves to the empty
    # permission set (see permissions.py's BUILTIN_ROLE_PERMISSIONS), the
    # correct fail-closed floor: a member who should have real access gets an
    # explicit role_id via PUT /members/{id}/role, same as any other role
    # assignment in this system.
    token_role = MEMBER_ROLE

    # Standalone 2FA design: does this member need TOTP before a full
    # session? Checked AFTER password verification succeeds (never before --
    # see test_login_does_not_leak_totp_enrollment_status_before_password_is_verified),
    # so a wrong password gets byte-identical 401s whether or not TOTP is
    # involved for this member at all.
    #
    # The DECISION lives in auth/totp_gate.py. What stays HERE is the
    # MINTING: only this function knows it is issuing a Community password
    # session, with this tenant's id, this token_role floor and
    # `kind="operator"` -- `totp_gate` never mints.
    async with tenant_session(org.id) as db:
        outcome = await totp_gate(db, member_id=member_id)

    provider = get_identity_provider()

    if outcome.requires_totp_code:
        # Outcome 3: a challenge is required, whatever the member's role.
        token = provider.mint(
            tenant_id=org.id,
            subject=body.email,
            role=token_role,
            kind="operator",
            scopes=["totp:challenge"],
        )
        principal = provider.verify(token)
        return PasswordSessionResponse(
            token=token,
            principal=principal,
            member_id=str(member_id),
            requires_totp_code=True,
        )

    if outcome.requires_totp_enrollment:
        # Outcome 2: a mandatory member's grace expired with nothing
        # enrolled -- refuse a full session.
        token = provider.mint(
            tenant_id=org.id,
            subject=body.email,
            role=token_role,
            kind="operator",
            scopes=["totp:enroll"],
        )
        principal = provider.verify(token)
        return PasswordSessionResponse(
            token=token,
            principal=principal,
            member_id=str(member_id),
            requires_totp_enrollment=True,
        )

    # Outcome 1: a full session, unchanged from before this feature existed.
    # `totp_grace_expires_at` is non-None only for a member whose clock is
    # actually running (the frontend's nag banner); an opt-in, non-mandatory
    # member has no clock at all and gets exactly the response they always got.
    token = provider.mint(
        tenant_id=org.id,
        subject=body.email,
        role=token_role,
        kind="operator",
    )
    principal = provider.verify(token)
    return PasswordSessionResponse(
        token=token,
        principal=principal,
        member_id=str(member_id),
        totp_grace_expires_at=(
            outcome.totp_grace_expires_at.isoformat()
            if outcome.totp_grace_expires_at is not None
            else None
        ),
    )


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[
        Depends(unguarded("logout is always available and is a no-op"))
    ],
)
async def password_logout() -> None:
    """Logout endpoint (no-op with stateless JWT).

    With JWT tokens, logout is handled client-side: the frontend simply discards
    the token. This endpoint exists for symmetry and client convenience.

    Raises: Nothing (always succeeds).
    """
    # Stateless JWT: no server-side session to revoke.
    # Client discards the token; subsequent requests without a token fail at
    # the authorization layer. Future work could implement token revocation via
    # a blacklist (see spec §8 rate limits + audit events).
    pass
