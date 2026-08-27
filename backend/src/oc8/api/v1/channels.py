"""Approval channels over HTTP (§5.6): bind an account, and hear back from one.

Two kinds of route with opposite trust, deliberately in one file so the
difference is impossible to miss.

The `/channels/...` routes below the router are ordinary authenticated API: an
operator asks for a binding code, lists what is bound, revokes one.

`/channels/{channel}/webhook/{tenant_id}` is the exception and the only one in
oc8: it is called by Telegram or Meta, not by a user, so it cannot carry an oc8
token. Everything about it is built on that:

* the platform's own signature is checked FIRST, by the plugin, and a call that
  fails is refused before it is parsed -- an unauthenticated webhook that
  reaches the decision path is an open door to approving anything;
* the tenant is in the path because a tenant runs its OWN bot, so nothing here
  has to read across tenants to work out whose approval this is;
* it answers 200 to almost everything. Telegram retries a non-200 and Meta
  disables a webhook that keeps failing, so an error here costs the channel
  itself. What did not happen is said in the chat, not in the status code.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from oc8 import models as m
from oc8.api.deps import CurrentPrincipal, DbSession, require_permission, unguarded
from oc8.audit import append_event
from oc8.authz.authority import granted_for_member
from oc8.authz.permissions import APPROVAL_DECIDE_ANY, CHANNEL, MANAGE, VIEW, perm
from oc8.authz.scope import scope_for_principal, subject_uuid_for
from oc8.channels import ChannelDecision, ChannelLink
from oc8.channels.binding import BindingError, issue_code, redeem_code, revoke
from oc8.channels.dispatch import decision_from
from oc8.channels.registry import channels_for_tenant
from oc8.db.session import tenant_session
from oc8.schemas.base import CamelModel

logger = logging.getLogger(__name__)

router = APIRouter()


class AvailableChannelDTO(CamelModel):
    id: str


class LinkCodeDTO(CamelModel):
    channel: str
    code: str
    expires_at: str


class BindingDTO(CamelModel):
    id: str
    channel: str
    user_id: str
    #: Deliberately NOT the account id. Which chat an approver reads is theirs;
    #: an operator needs to know a binding exists and be able to revoke it.
    bound: bool
    created_at: str


class WebhookAck(BaseModel):
    ok: bool = True


@router.get(
    "/channels",
    response_model=list[AvailableChannelDTO],
    dependencies=[Depends(require_permission(perm(CHANNEL, VIEW)))],
)
async def list_available_channels(
    db: DbSession, principal: CurrentPrincipal
) -> list[AvailableChannelDTO]:
    """Which channel ids this tenant can actually link to right now.

    Reuses `channels_for_tenant` (the same discovery `create_link_code` and the
    inbound webhook already trust) rather than hardcoding "telegram"/"whatsapp"
    here -- a plugin-contributed channel this tenant installed and enabled shows
    up with zero changes to this endpoint, matching how the credential-type
    registry stays capa-agnostic.
    """
    channels = await channels_for_tenant(db, tenant_id=principal.tenant_id)
    return [AvailableChannelDTO(id=channel_id) for channel_id in sorted(channels)]


@router.post(
    "/channels/{channel}/link",
    response_model=LinkCodeDTO,
    dependencies=[Depends(require_permission(perm(CHANNEL, MANAGE)))],
)
async def create_link_code(channel: str, db: DbSession, principal: CurrentPrincipal) -> LinkCodeDTO:
    """A code for the caller to send to the bot. Theirs alone -- it binds THEIR
    account, so it is minted for the authenticated subject and shown once.

    This is also where the messenger door gets its authority, and the only place
    it could: the webhook carries no token, so `approval:decide_any` -- the claim
    that makes a CEO unrestricted -- is unreadable there. Rather than mirror the
    role on every request (two sources of truth for one fact, stale in exactly
    the direction that costs money), it is written ONCE here, at an authenticated
    moment, as a durable and revocable row, and audited.
    """
    channels = await channels_for_tenant(db, tenant_id=principal.tenant_id)
    if channel not in channels:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such approval channel")
    try:
        member, _scope = await scope_for_principal(db, principal, upsert=True)
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    if member is None:  # pragma: no cover - `upsert=True` mints or raises
        # An `assert` and not this line until now, on a value the next statement
        # hands `all_departments` to. `python -O` strips asserts, so under it that
        # degraded to an AttributeError and a 500 -- the same reason
        # `scope_for_principal` raises `PermissionError` rather than asserting its
        # `kind` (`authz/scope.py`). It fails closed either way; it should fail
        # closed on purpose.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "could not resolve the caller")

    # RESOLVED, not read off the token. This line writes a DURABLE ROW granting
    # company-wide decide authority, and the row outlives whatever authorised it
    # -- so a demoted administrator minting a link code here would have kept
    # `all_departments = True` after the assignment that took it away, and
    # revoking the role afterwards would not have taken it back. It is the one
    # place in the system where reading the wrong source is not merely a wrong
    # answer but a permanent one.
    granted, _source = await granted_for_member(db, principal, member)
    if APPROVAL_DECIDE_ANY in granted and not member.all_departments:
        member.all_departments = True
        await append_event(
            db,
            tenant_id=principal.tenant_id,
            actor_type="operator",
            actor_id=member.id,
            category="member",
            action="member.all_departments_granted",
            resource={"member_id": str(member.id), "channel": channel},
            # "requested a link code", not "linked": the code is minted here and
            # redeemed later in the messenger, and it may never be. The audit line
            # said "linked an approval channel" for an act that had not happened
            # yet -- and this row is a grant of company-wide decide authority, so
            # the sentence beside it has to be the one that is true.
            reason="requested an approval-channel link code while holding approval:decide_any",
            principal=principal,
        )

    row = await issue_code(
        db,
        tenant_id=principal.tenant_id,
        user_id=_subject_id(principal),
        channel=channel,
        # Who this phone speaks for. Without it the binding is one nobody stands
        # behind, and `dispatch.decision_from` refuses it.
        member_id=member.id,
    )
    await db.commit()
    assert row.code is not None and row.code_expires_at is not None
    return LinkCodeDTO(channel=channel, code=row.code, expires_at=row.code_expires_at.isoformat())


@router.get(
    "/channels/bindings",
    response_model=list[BindingDTO],
    dependencies=[Depends(require_permission(perm(CHANNEL, VIEW)))],
)
async def list_bindings(db: DbSession, principal: CurrentPrincipal) -> list[BindingDTO]:
    """Every live binding in the tenant. An operator has to be able to see who
    can approve from a phone -- that is the question an audit asks first."""
    rows = (
        (
            await db.execute(
                select(m.ApprovalChannelBinding).where(
                    m.ApprovalChannelBinding.revoked_at.is_(None),
                    m.ApprovalChannelBinding.external_id.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        BindingDTO(
            id=str(r.id),
            channel=r.channel,
            user_id=str(r.user_id),
            bound=True,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


@router.delete(
    "/channels/bindings/{binding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(perm(CHANNEL, MANAGE)))],
)
async def revoke_binding(binding_id: uuid.UUID, db: DbSession, principal: CurrentPrincipal) -> None:
    row = await revoke(db, tenant_id=principal.tenant_id, binding_id=binding_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such binding")
    await db.commit()


@router.post(
    "/channels/{channel}/webhook/{tenant_id}",
    response_model=WebhookAck,
    dependencies=[
        Depends(
            unguarded("authenticated by the messenger's webhook signature, not by an operator role")
        )
    ],
)
async def webhook(channel: str, tenant_id: uuid.UUID, request: Request) -> WebhookAck:
    """A platform calling in. See the module docstring for why this is different.

    Its own session rather than the request-scoped one: there is no principal to
    derive a tenant from, so the tenant comes from the path and the session is
    bound to it explicitly.
    """
    body = await request.body()
    async with tenant_session(tenant_id) as db:
        channels = await channels_for_tenant(db, tenant_id=tenant_id)
        impl = channels.get(channel)
        if impl is None:
            # 404 rather than a hint: an unconfigured channel and a wrong tenant
            # id should look identical from outside.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")

        if not impl.verify_inbound(headers=dict(request.headers), body=body):
            logger.warning("rejected an unsigned %s webhook for %s", channel, tenant_id)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not found")

        try:
            update: Any = await request.json()
        except Exception:
            return WebhookAck()
        if not isinstance(update, dict):
            return WebhookAck()

        parsed = impl.parse_inbound(update)
        if parsed is None:
            # Somebody typing at the bot, a delivery receipt, the platform's own
            # housekeeping. Normal, and not an error.
            return WebhookAck()

        if isinstance(parsed, ChannelLink):
            await _bind(db, impl, tenant_id=tenant_id, channel=channel, link=parsed)
        elif isinstance(parsed, ChannelDecision):
            await _decide(db, impl, tenant_id=tenant_id, channel=channel, decision=parsed)
        return WebhookAck()


async def _bind(
    db: Any, impl: Any, *, tenant_id: uuid.UUID, channel: str, link: ChannelLink
) -> None:
    try:
        await redeem_code(
            db,
            tenant_id=tenant_id,
            channel=channel,
            code=link.code,
            external_id=link.external_id,
        )
        await db.commit()
        await _say(impl, link.external_id, "Verbunden. Freigaben kommen ab jetzt hier an.")
    except BindingError:
        await db.rollback()
        # The same sentence whatever went wrong -- expired, spent, never issued.
        # Anything more specific turns the bot into an oracle for live codes.
        await _say(impl, link.external_id, "Dieser Code gilt nicht.")


async def _decide(
    db: Any, impl: Any, *, tenant_id: uuid.UUID, channel: str, decision: ChannelDecision
) -> None:
    from oc8.approvals import AlreadyDecided, ApprovalError, NotYourDepartment, NotYourSayAtAll

    try:
        result = await decision_from(
            db,
            tenant_id=tenant_id,
            channel_id=channel,
            external_id=decision.external_id,
            approval_id=decision.approval_id,
            verdict=decision.verdict,
            option_key=decision.option_key,
            # Parsed by the plugin, carried this far, and dropped on the floor
            # here until now. It matters because the screen makes a reason
            # REQUIRED to reject -- a rejection with no reason is a dead end for
            # the agent that has to act on it -- and the phone must not be the way
            # round that.
            reason=decision.reason,
        )
    # `NotYourDepartment` is the funnel's defence in depth and is unreachable
    # while `decision_from` checks `may_decide` first. Answered with the SAME
    # sentence anyway: if that pre-check ever drifts, the bot must still not
    # start telling "not yours" apart from "no such approval".
    #
    # `NotYourSayAtAll` IS reachable here and always will be: a messenger message
    # carries no token, so no `ChannelActor` holds `budget:manage` or
    # `agent:manage`, and a budget incident or a hire request tapped from a phone
    # is refused however unrestricted its sender is. Folded into the same sentence
    # rather than explained, because the phone must never become the place where
    # somebody learns which permission would have worked.
    except (PermissionError, NotYourDepartment, NotYourSayAtAll):
        await db.rollback()
        await _say(impl, decision.external_id, "Dazu bist du hier nicht berechtigt.")
        return
    except AlreadyDecided as exc:
        await db.rollback()
        # A normal outcome, not a failure: several people can have this open at
        # once, and the one who was slower deserves to be told what happened
        # rather than that they did something wrong.
        when = f" ({exc.decided_at:%d.%m. %H:%M})" if exc.decided_at else ""
        await _say(impl, decision.external_id, f"Schon entschieden: {exc.status}{when}.")
        return
    except ApprovalError:
        await db.rollback()
        await _say(impl, decision.external_id, "Das konnte nicht übernommen werden.")
        return

    run_id = result.resumed_run_id
    await db.commit()
    if run_id is not None:
        # Published only after the commit: a stream entry whose run row is not
        # yet visible is a run a worker picks up and cannot find.
        from oc8.runtime.intake import publish_run

        await publish_run(run_id=run_id, tenant_id=tenant_id)
    await _say(impl, decision.external_id, f"Übernommen: {result.approval.status}.")


async def _say(impl: Any, external_id: str, text: str) -> None:
    """Tell the sender what happened, if the plugin can. Best-effort: the
    decision is already recorded and a silent bot must not undo it."""
    say = getattr(impl, "say", None)
    if say is None:
        return
    try:
        await say(external_id, text)
    except Exception:
        logger.warning("could not reply on an approval channel", exc_info=True)


def _subject_id(principal: Any) -> uuid.UUID:
    """The authenticated user as a uuid.

    Derived from the subject when it is not already one (dev logins use a name),
    so a binding is always attributable to something stable rather than to
    whatever string an identity provider happened to send.

    The derivation itself lives in `authz.scope.subject_uuid_for`, which is what
    `org_member.subject_uuid` is written from. It was duplicated here, and the
    day the two copies disagreed every binding in the system would have pointed
    at a person who does not exist.
    """
    return subject_uuid_for(str(getattr(principal, "subject", "") or ""))
