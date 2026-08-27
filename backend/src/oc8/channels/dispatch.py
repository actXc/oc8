"""Telling every bound approver that something is waiting (§5.6).

Best-effort by design, in both directions:

* a messenger that is down must never stop an approval from being RAISED. The
  inbox is the system of record and it is always there; an approval that failed
  to send is a nuisance, an approval that failed to exist is a lost decision.
* a delivery that fails is logged, not retried forever. The escalation ladder in
  §5.5 already re-notifies unactioned requests, so a second mechanism here would
  only produce duplicate messages for the same silence.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.agent.decision_followup import option_labels
from oc8.channels import binding
from oc8.channels.base import ApprovalChannel
from oc8.channels.notice import ApprovalNotice, NoticeOption, outranks

logger = logging.getLogger(__name__)

#: The one thing this door ever says when it says no. Named once so the five
#: places that raise it cannot drift into five distinguishable sentences --
#: `tests/channels/test_channel_decision_is_scoped.py` compares two of them for
#: string equality, because a bot that tells refusals apart is a bot that answers
#: "does this approval exist" to anybody who found its address.
REFUSED = "this sender may not decide anything here"


def notice_for(approval: m.ApprovalRequest) -> ApprovalNotice:
    """The neutral description of one request. Nothing messenger-shaped here."""
    labels = option_labels(approval) if approval.action_type == "decision" else {}
    return ApprovalNotice(
        approval_id=approval.id,
        tenant_id=approval.tenant_id,
        title=approval.title or "Eine Entscheidung wartet",
        detail=approval.detail or "",
        amount_text=approval.amount_text or "",
        # `option_labels` returns the whole option object; a channel only needs
        # something to put on a button, and falls back to the key so an option
        # authored without a label is still answerable rather than invisible.
        options=tuple(
            NoticeOption(key=k, label=str(v.get("label") or k)) for k, v in labels.items()
        ),
        expires_at=approval.expires_at,
        classification=_classification_of(approval),
    )


def _classification_of(approval: m.ApprovalRequest) -> str:
    """How sensitive this request's text is.

    Read from the payload when whoever raised it said so, `internal` otherwise --
    NOT `public`. Guessing downward here would be the one place in this design
    where an unstated value widens what may leave the building.
    """
    payload = approval.payload if isinstance(approval.payload, dict) else {}
    value = payload.get("classification")
    return value if isinstance(value, str) and value else "internal"


async def announce(
    db: AsyncSession,
    approval: m.ApprovalRequest,
    *,
    channels: dict[str, ApprovalChannel],
) -> dict[str, dict[str, str]]:
    """Tell everyone bound to an enabled channel.

    Returns channel -> {account: message handle}. Keyed by ACCOUNT, not by
    position: `close_out` runs after a human has had time to act, and by then
    somebody may have bound or revoked an account -- pairing two lists by index
    would then withdraw the wrong message from the wrong chat.

    Never raises. A caller is in the middle of parking an agent's run; nothing
    about a chat bot is allowed to interfere with that.
    """
    notice = notice_for(approval)
    handles: dict[str, dict[str, str]] = {}
    for channel_id, channel in channels.items():
        try:
            caps = channel.capabilities()
            outgoing = notice
            if outranks(notice.classification, caps.max_classification):
                # Still sent, without the content. That something is waiting is
                # the whole value of the channel; WHAT it is can wait for a
                # screen that is allowed to show it.
                outgoing = notice.redacted()
                logger.info(
                    "approval %s goes to %s as a pointer: %s outranks %s",
                    approval.id,
                    channel_id,
                    notice.classification,
                    caps.max_classification,
                )
            people = await binding.recipients(
                db,
                tenant_id=approval.tenant_id,
                channel=channel_id,
                # Being told is a disclosure too: the notice carries the title and
                # the amount. A fan-out that ignored this put Engineering's held
                # tool call on every phone in the company before anybody decided
                # anything.
                department_id=approval.department_id,
            )
            sent: dict[str, str] = {}
            for person in people:
                if person.external_id is None:
                    continue
                try:
                    handle = await channel.deliver(outgoing, external_id=person.external_id)
                except Exception:
                    # One approver unreachable is not everybody unreachable.
                    logger.warning(
                        "approval %s could not be delivered to a %s recipient",
                        approval.id,
                        channel_id,
                        exc_info=True,
                    )
                    continue
                if handle:
                    sent[person.external_id] = handle
            if sent:
                handles[channel_id] = sent
        except Exception:
            logger.warning(
                "approval channel %s failed while announcing %s",
                channel_id,
                approval.id,
                exc_info=True,
            )
    return handles


async def close_out(
    db: AsyncSession,
    approval: m.ApprovalRequest,
    *,
    channels: dict[str, ApprovalChannel],
    outcome: str,
    handles: dict[str, dict[str, str]] | None = None,
) -> None:
    """Withdraw the question everywhere it is still standing.

    Called after a decision arrives, whichever channel carried it. Without this
    the request sits in the other chats looking open, and the next person to
    answer is told they were too late by a system that could have said so.
    """
    notice = notice_for(approval)
    by_channel = handles or {}
    for channel_id, channel in channels.items():
        try:
            # The same RULE the announcement used, recomputed -- which is not the
            # same thing as the same audience, and the difference is stated rather
            # than glossed. A seat granted between the announcement and the
            # decision gets a withdrawal for a message it never received (`handle`
            # is then None, so the channel posts rather than edits); a seat revoked
            # in between keeps a message that still reads as pending.
            #
            # Recomputed on purpose: `channel_handles` records only the accounts
            # whose delivery returned a handle, so driving the withdrawal off it
            # alone would stop telling everybody whose channel returned an empty
            # one -- and `approvals/service.py` already consumes that map for the
            # narrower job of editing the exact message. Closing the window means
            # recording the announced audience itself, which is §10's escalation
            # ladder work, not a comment.
            people = await binding.recipients(
                db,
                tenant_id=approval.tenant_id,
                channel=channel_id,
                department_id=approval.department_id,
            )
            known = by_channel.get(channel_id) or {}
            for person in people:
                if person.external_id is None:
                    continue
                await channel.withdraw(
                    notice,
                    external_id=person.external_id,
                    handle=known.get(person.external_id),
                    outcome=outcome,
                )
        except Exception:
            logger.warning(
                "approval channel %s could not withdraw %s", channel_id, approval.id, exc_info=True
            )


async def decision_from(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    channel_id: str,
    external_id: str,
    approval_id: uuid.UUID,
    verdict: str,
    option_key: str | None = None,
    reason: str | None = None,
) -> Any:
    """An answer that arrived over a channel, taken through the SAME path as the
    inbox (`oc8.approvals.decide_approval`).

    Returns the `DecisionResult`. Raises `PermissionError` with ONE sentence for
    every refusal there is: unknown sender, a binding that names nobody, a person
    who has been removed, an approval that does not exist, and an approval in a
    department this person does not stand in. They are indistinguishable on
    purpose -- the approval id travels in the callback data of every message the
    phone has ever received, so a refusal that told them apart would make the bot
    a directory of which approvals exist and whose they are.

    Until this slice the only question asked was whether a live binding existed,
    so any bound phone in the tenant could decide every approval in it.
    """
    from oc8.approvals import NotYourSayAtAll, decide_approval
    from oc8.approvals.repo import load_for_actor
    from oc8.authz.scope import ChannelActor, scope_for_binding

    who = await binding.resolve(
        db, tenant_id=tenant_id, channel=channel_id, external_id=external_id
    )
    if who is None:
        raise PermissionError(REFUSED)

    # The messenger door has no token, so the ONLY term available here is the row:
    # the member this binding was issued for, their `all_departments` flag and
    # their live seats. `scope_for_binding` returns `(None, empty)` -- never a
    # different exception -- for a binding with no member, a removed member and a
    # member in another tenant, so all three collapse into the sentence above.
    member, scope = await scope_for_binding(db, who)
    if member is None:
        raise PermissionError(REFUSED)
    actor = ChannelActor(binding=who, member=member, scope=scope, via=channel_id)

    approval = await load_for_actor(db, approval_id, actor=actor)
    if approval is None or approval.tenant_id != tenant_id:
        raise PermissionError(REFUSED)
    # `load_for_actor` answers may_VIEW; a dept_viewer may be told and may not
    # answer. Checked here rather than left to `decide_approval` because the
    # funnel's refusal is a different exception with a different message, and at
    # this door every refusal has to read the same.
    if not scope.may_decide(approval.department_id):
        raise PermissionError(REFUSED)

    try:
        return await decide_approval(
            db,
            approval,
            decision=verdict,
            tenant_id=tenant_id,
            actor=actor,
            reason=reason,
            option=option_key,
        )
    except NotYourSayAtAll as exc:
        # Collapsed into this door's ONE refusal, rather than allowed out with the
        # permission it names. `NotYourSayAtAll` is answerable out loud over HTTP
        # -- the caller is looking at his own queue -- but here the approval id
        # travels in the callback data of every message the phone has ever
        # received, so a refusal that read differently would tell a sender which
        # of those ids are budget incidents and which are held tool calls.
        #
        # It is REACHABLE and always will be: no `ChannelActor` holds a
        # tenant-wide permission, because a messenger message carries no token, so
        # every `budget_incident` and every `hire_agent` tapped from a phone lands
        # here however unrestricted its sender is.
        raise PermissionError(REFUSED) from exc
