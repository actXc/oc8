"""The shared TOTP-gate decision for `password_login` (Community's local
password auth, the only login entry point this edition ships). This module
only DECIDES, given an already-resolved member, what happens next -- it does
not mint, does not read a token, and does not know which route called it.
Kept as its own module (rather than inlined into `password_login`) so a
second entry point could reuse the identical decision without duplicating
the grace-period comparison, which is exactly the kind of thing that drifts
by a day, or by a boundary, without any test noticing.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.auth.principal import Principal

#: How long a newly-mandatory org_admin has to enroll before a login entry
#: point stops handing out a full session (standalone 2FA design). Defined
#: HERE, once, rather than in each caller: this is the number both entry
#: points must agree on, and two copies of it is two deadlines. Not yet
#: tenant-configurable -- the spec doesn't ask for that, and a named
#: constant is easy for a later task to promote to a setting.
TOTP_GRACE_DAYS = 7


@dataclass(frozen=True)
class TotpGateOutcome:
    """Exactly one of the three outcomes, plus the nag deadline.

    `full_session_ok` and the two `requires_*` flags are mutually exclusive
    by construction (every `return` below sets exactly one shape), so a
    caller may branch on them in any order.
    """

    #: True if the caller may proceed with a full session unchanged.
    full_session_ok: bool
    #: True if a TOTP credential is already enrolled: the caller owes us a
    #: code before any session at all (outcome 3).
    requires_totp_code: bool = False
    #: True if a mandatory member's grace period has run out with nothing
    #: enrolled: the caller owes us an enrollment (outcome 2).
    requires_totp_enrollment: bool = False
    #: Non-None only alongside `full_session_ok` for a member whose grace
    #: clock is running: the deadline, for the frontend's nag banner.
    totp_grace_expires_at: dt.datetime | None = None


async def member_id_for_principal(db: AsyncSession, principal: Principal) -> uuid.UUID | None:
    """The caller's own member row id, or None if they have none yet.

    Lives beside `totp_gate` for the same reason `totp_gate` itself does:
    a shared query is one place to get the `deleted_at IS NULL` term right,
    rather than a chance for a second copy to lose it.

    None rather than a 404 is deliberate: a member row that does not exist
    can carry neither a `TotpCredential` nor a `totp_grace_started_at`, so
    the gate's answer would be `full_session_ok` anyway. `GET /me` is what
    mints the row, and nothing orders it against a brand-new operator's
    first request.
    """
    return (
        await db.execute(
            select(m.OrgMember.id).where(
                m.OrgMember.tenant_id == principal.tenant_id,
                m.OrgMember.subject == principal.subject,
                m.OrgMember.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def totp_gate(db: AsyncSession, *, member_id: uuid.UUID) -> TotpGateOutcome:
    """The three-outcome decision, in the order the design fixes.

    Enrollment is checked FIRST and unconditionally: an enrolled member owes
    a code whatever their role and whatever their grace clock says, so the
    clock is never even read for them. That ordering is load-bearing, not
    incidental -- reading the clock first would let a stale
    `totp_grace_started_at` on an already-enrolled member (the invariant
    `POST /auth/totp/confirm` clears, and which nothing else does) demand a
    fresh enrollment from somebody who has one.

    Only a member with a non-NULL `totp_grace_started_at` is mandatory at
    all. That column is written by `assign_role`/`password_setup` the moment
    someone becomes org_admin; an opt-in, non-admin member never has one and
    always lands on `full_session_ok` here.
    """
    cred = (
        await db.execute(select(m.TotpCredential).where(m.TotpCredential.member_id == member_id))
    ).scalar_one_or_none()
    if cred is not None and cred.enrolled_at is not None:
        return TotpGateOutcome(full_session_ok=False, requires_totp_code=True)

    member_row = await db.get(m.OrgMember, member_id)
    grace_started = member_row.totp_grace_started_at if member_row is not None else None
    if grace_started is None:
        return TotpGateOutcome(full_session_ok=True)

    grace_deadline = grace_started + dt.timedelta(days=TOTP_GRACE_DAYS)
    if dt.datetime.now(tz=dt.UTC) >= grace_deadline:
        return TotpGateOutcome(full_session_ok=False, requires_totp_enrollment=True)
    return TotpGateOutcome(full_session_ok=True, totp_grace_expires_at=grace_deadline)
