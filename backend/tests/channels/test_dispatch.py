"""Announcing an approval on a channel, and taking the answer back.

The properties here are the ones that decide whether a channel is safe to switch
on at all: what leaves the building, what happens when the messenger is down,
and who is allowed to answer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from oc8 import models as m
from oc8.authz.scope import subject_uuid_for
from oc8.channels import ChannelCapabilities
from oc8.channels.binding import issue_code, redeem_code
from oc8.channels.dispatch import (
    _CONTENT_WITHHELD,
    announce,
    close_out,
    decision_from,
    notice_for,
    tell_sender_gated,
)
from oc8.channels.notice import ApprovalNotice
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

CHANNEL = "fake"


@dataclass
class FakeChannel:
    channel_id: str = CHANNEL
    caps: ChannelCapabilities = field(default_factory=ChannelCapabilities)
    delivered: list[tuple[str, ApprovalNotice]] = field(default_factory=list)
    withdrawn: list[tuple[str, str | None, str]] = field(default_factory=list)
    said: list[tuple[str, str]] = field(default_factory=list)
    explode_for: str | None = None

    def capabilities(self) -> ChannelCapabilities:
        return self.caps

    async def deliver(self, notice: ApprovalNotice, *, external_id: str) -> str | None:
        if self.explode_for == external_id:
            raise RuntimeError("the messenger is down")
        self.delivered.append((external_id, notice))
        return f"msg-{external_id}"

    async def withdraw(
        self, notice: ApprovalNotice, *, external_id: str, handle: str | None, outcome: str
    ) -> None:
        self.withdrawn.append((external_id, handle, outcome))

    async def say(self, external_id: str, text: str) -> None:
        self.said.append((external_id, text))


async def _approval(db: Any, tenant: uuid.UUID, **payload: Any) -> m.ApprovalRequest:
    ar = m.ApprovalRequest(
        tenant_id=tenant,
        agent_id=uuid.uuid4(),
        action_type="decision",
        payload=payload,
        status="pending",
        title="Erstattung freigeben?",
        detail="Ticket #42, 249 EUR doppelt abgebucht, Kundin Berger.",
        amount_text="249,00 EUR",
    )
    db.add(ar)
    await db.flush()
    return ar


async def _bind(db: Any, tenant: uuid.UUID, external_id: str) -> None:
    """One person, unrestricted, with this account bound to them.

    `all_departments` because the approvals in this file carry no department at
    all (they are raised straight into the table with a random `agent_id`), and a
    tenant-wide approval reaches only the unrestricted. The tests below are about
    classification, handles and refusal messages -- the department term has its
    own file, `test_channel_decision_is_scoped.py`. What this DOES pin is that a
    binding needs a person behind it to be told anything at all.
    """
    member = m.OrgMember(
        tenant_id=tenant,
        subject=f"person-{external_id}",
        subject_uuid=subject_uuid_for(f"person-{external_id}"),
        all_departments=True,
    )
    db.add(member)
    await db.flush()
    row = await issue_code(
        db, tenant_id=tenant, user_id=member.subject_uuid, channel=CHANNEL, member_id=member.id
    )
    assert row.code is not None
    await redeem_code(db, tenant_id=tenant, channel=CHANNEL, code=row.code, external_id=external_id)


async def test_material_above_a_channels_ceiling_travels_as_a_pointer(
    app_session: AppSessionFactory,
) -> None:
    """A fresh channel may carry `public` only, and approvals are `internal` --
    so by default the message says something is waiting and nothing about what.
    That friction is deliberate: the convenient default puts customer data in a
    chat and nobody notices until it matters."""
    tenant = uuid.uuid4()
    channel = FakeChannel()
    async with app_session(tenant) as db:
        ar = await _approval(db, tenant)
        await _bind(db, tenant, "chat-1")
        await announce(db, ar, channels={CHANNEL: channel})

    assert len(channel.delivered) == 1
    _who, sent = channel.delivered[0]
    assert sent.content_withheld is True
    assert sent.detail == "" and sent.amount_text == ""
    assert "Erstattung" in sent.title, "the approver still learns something is waiting"


async def test_a_channel_allowed_to_carry_it_gets_the_whole_request(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    channel = FakeChannel(caps=ChannelCapabilities(max_classification="internal"))
    async with app_session(tenant) as db:
        ar = await _approval(db, tenant, options=[{"key": "full", "label": "Voll erstatten"}])
        await _bind(db, tenant, "chat-1")
        await announce(db, ar, channels={CHANNEL: channel})

    _who, sent = channel.delivered[0]
    assert sent.content_withheld is False
    assert "249 EUR" in sent.detail
    assert [o.key for o in sent.options] == ["full"]
    assert [o.label for o in sent.options] == ["Voll erstatten"]


async def test_one_unreachable_approver_does_not_silence_the_others(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    channel = FakeChannel(explode_for="chat-1")
    async with app_session(tenant) as db:
        ar = await _approval(db, tenant)
        await _bind(db, tenant, "chat-1")
        await _bind(db, tenant, "chat-2")
        handles = await announce(db, ar, channels={CHANNEL: channel})

    assert [w for w, _ in channel.delivered] == ["chat-2"]
    assert handles[CHANNEL] == {"chat-2": "msg-chat-2"}


async def test_a_messenger_that_is_down_never_stops_the_approval(
    app_session: AppSessionFactory,
) -> None:
    """The inbox is the record and is always there. An approval that failed to
    send is a nuisance; one that failed to exist is a lost decision."""

    class Broken:
        channel_id = CHANNEL

        def capabilities(self) -> ChannelCapabilities:
            raise RuntimeError("no config")

        async def deliver(self, notice: ApprovalNotice, *, external_id: str) -> str | None:
            raise AssertionError("never reached")

        async def withdraw(self, *a: Any, **k: Any) -> None:
            return None

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        ar = await _approval(db, tenant)
        await _bind(db, tenant, "chat-1")
        assert await announce(db, ar, channels={CHANNEL: Broken()}) == {}


async def test_the_question_is_withdrawn_from_the_account_it_was_sent_to(
    app_session: AppSessionFactory,
) -> None:
    """Handles are keyed by account, not by position: by the time somebody
    answers, an account may have been bound or revoked, and pairing two lists by
    index would withdraw the wrong message from the wrong chat."""
    tenant = uuid.uuid4()
    channel = FakeChannel(caps=ChannelCapabilities(max_classification="internal"))
    async with app_session(tenant) as db:
        ar = await _approval(db, tenant)
        await _bind(db, tenant, "chat-1")
        await _bind(db, tenant, "chat-2")
        handles = await announce(db, ar, channels={CHANNEL: channel})
        await close_out(db, ar, channels={CHANNEL: channel}, outcome="approved", handles=handles)

    assert ("chat-1", "msg-chat-1", "approved") in channel.withdrawn
    assert ("chat-2", "msg-chat-2", "approved") in channel.withdrawn


async def test_an_unbound_sender_decides_nothing_and_learns_nothing(
    app_session: AppSessionFactory,
) -> None:
    """The refusal must not distinguish "unknown sender" from "unknown
    approval": either would tell whoever found the bot which approvals exist."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        ar = await _approval(db, tenant)
        with pytest.raises(PermissionError) as stranger:
            await decision_from(
                db,
                tenant_id=tenant,
                channel_id=CHANNEL,
                external_id="nobody",
                approval_id=ar.id,
                verdict="approve",
            )
        await _bind(db, tenant, "chat-1")
        with pytest.raises(PermissionError) as ghost:
            await decision_from(
                db,
                tenant_id=tenant,
                channel_id=CHANNEL,
                external_id="chat-1",
                approval_id=uuid.uuid4(),
                verdict="approve",
            )
        assert str(stranger.value) == str(ghost.value)

        fresh = await db.get(m.ApprovalRequest, ar.id)
        assert fresh is not None and fresh.status == "pending"


async def test_a_notice_carries_no_content_it_was_not_given() -> None:
    """`redacted()` has to remove the options too. A button labelled "Voll
    erstatten" tells you what the request is about even with the text gone."""
    notice = ApprovalNotice(
        approval_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        title="Entscheidung",
        detail="249 EUR an Frau Berger erstatten",
        amount_text="249,00 EUR",
        options=(),
    )
    hidden = notice.redacted()
    assert hidden.detail == "" and hidden.amount_text == "" and hidden.options == ()
    assert hidden.approval_id == notice.approval_id, "it is still answerable in the inbox"


async def test_an_approval_with_no_stated_sensitivity_is_treated_as_internal() -> None:
    """Guessing downward here would be the one place an unstated value widens
    what may leave the building."""
    ar = m.ApprovalRequest(
        tenant_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        action_type="tool_send",
        payload={},
        status="pending",
        title="t",
        detail="d",
    )
    assert notice_for(ar).classification == "internal"


async def test_an_assistant_reply_above_the_channels_ceiling_is_withheld() -> None:
    """`tell_sender_gated` applies the exact same `outranks` check `announce`
    already applies to an approval's `detail` -- an Assistant-produced reply
    (ack, final answer, park/failure notice) is `internal`, so a channel left
    at the real default (`max_classification="public"`) must not carry it."""
    channel = FakeChannel(caps=ChannelCapabilities(max_classification="public"))
    await tell_sender_gated(channel, "42", "17 offene Tickets: ...")
    assert channel.said == [("42", _CONTENT_WITHHELD)]


async def test_an_assistant_reply_within_the_channels_ceiling_goes_through() -> None:
    channel = FakeChannel(caps=ChannelCapabilities(max_classification="internal"))
    await tell_sender_gated(channel, "42", "17 offene Tickets: ...")
    assert channel.said == [("42", "17 offene Tickets: ...")]


async def test_a_channel_widened_past_internal_still_carries_it() -> None:
    channel = FakeChannel(caps=ChannelCapabilities(max_classification="confidential"))
    await tell_sender_gated(channel, "42", "17 offene Tickets: ...")
    assert channel.said == [("42", "17 offene Tickets: ...")]
