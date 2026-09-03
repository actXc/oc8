"""The messenger door, which has no token at all.

`dispatch.decision_from` checks that a live binding exists and nothing else
(`channels/dispatch.py:186-194`), so today any bound phone in the tenant decides
every approval in it -- including the ones it was never told about, because the
approval id is in the callback data of any message that phone ever received.

And `binding.recipients` filters on tenant + channel only
(`channels/binding.py:166-183`), so a Head of Sales who bound Telegram is
messaged about Engineering's held tool calls, with the title and the amount in
the notification.

The refusals here are all ONE sentence. An error that distinguishes "unknown
sender" from "no member behind this binding" from "another department's
approval" turns the bot into a probe for which approvals exist, which is the
property `BindingError` is already built around.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth.principal import Principal
from oc8.authz.permissions import SEAT_APPROVER
from oc8.channels import ChannelCapabilities, ChannelDecision
from oc8.channels import binding as binding_mod
from oc8.channels.base import ApprovalChannel
from oc8.channels.dispatch import announce, decision_from
from oc8.channels.notice import ApprovalNotice
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

CHANNEL = "fake"


@dataclass
class FakeChannel:
    channel_id: str = CHANNEL
    caps: ChannelCapabilities = field(
        default_factory=lambda: ChannelCapabilities(max_classification="internal")
    )
    parsed: Any = None
    delivered: list[tuple[str, ApprovalNotice]] = field(default_factory=list)
    said: list[tuple[str, str]] = field(default_factory=list)

    def capabilities(self) -> ChannelCapabilities:
        return self.caps

    async def verify_inbound(self, *, headers: dict[str, str], body: bytes) -> bool:
        return True

    def parse_inbound(self, update: dict[str, Any]) -> Any:
        return self.parsed

    async def deliver(self, notice: ApprovalNotice, *, external_id: str) -> str | None:
        self.delivered.append((external_id, notice))
        return f"msg-{external_id}"

    async def withdraw(self, *a: Any, **k: Any) -> None:
        return None

    async def say(self, external_id: str, text: str) -> None:
        self.said.append((external_id, text))


def _only(channel: FakeChannel) -> dict[str, ApprovalChannel]:
    """`announce` takes the registry's mapping; a structural fake satisfies it
    at runtime but not nominally."""
    return {CHANNEL: cast(ApprovalChannel, channel)}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    from oc8.main import create_app

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


def _subject_uuid(subject: str) -> uuid.UUID:
    try:
        return uuid.UUID(subject)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"oc8:subject:{subject}")


async def _person_with_a_phone(
    db: Any,
    tenant: uuid.UUID,
    *,
    subject: str,
    department_id: uuid.UUID | None,
    external_id: str,
    with_member: bool = True,
) -> m.OrgMember | None:
    """A member, optionally a seat, and a live binding pointing at them.

    `with_member=False` writes the binding that migration 0046 deliberately does
    NOT grandfather: an existing row from before this slice, with `member_id`
    still NULL.
    """
    member: m.OrgMember | None = None
    if with_member:
        member = m.OrgMember(
            tenant_id=tenant,
            subject=subject,
            subject_uuid=_subject_uuid(subject),
            display_name=subject,
        )
        db.add(member)
        await db.flush()
        if department_id is not None:
            db.add(
                m.OrgMemberDepartment(
                    tenant_id=tenant,
                    member_id=member.id,
                    department_id=department_id,
                    seat_role=SEAT_APPROVER,
                )
            )
    db.add(
        m.ApprovalChannelBinding(
            tenant_id=tenant,
            channel=CHANNEL,
            user_id=_subject_uuid(subject),
            external_id=external_id,
            member_id=member.id if member is not None else None,
        )
    )
    await db.flush()
    return member


async def _approval(
    db: Any, tenant: uuid.UUID, *, department_id: uuid.UUID | None, title: str
) -> m.ApprovalRequest:
    agent = m.Agent(
        tenant_id=tenant, department_id=department_id or uuid.uuid4(), name=f"agent-{title[:8]}"
    )
    db.add(agent)
    await db.flush()
    row = m.ApprovalRequest(
        tenant_id=tenant,
        agent_id=agent.id,
        department_id=department_id,
        action_type="tool_send",
        status="pending",
        title=title,
        detail="4.320,00 EUR an Gartenholz GmbH",
        amount_text="4.320,00 EUR",
        payload={"tool": "odoo.send_quotation", "arguments": {}},
    )
    db.add(row)
    await db.flush()
    return row


# ----------------------------------------------------------------------- 21


async def test_a_bound_sender_cannot_decide_outside_their_seats(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = m.Department(tenant_id=tenant, name="Vertrieb")
        engineering = m.Department(tenant_id=tenant, name="Entwicklung")
        db.add_all([sales, engineering])
        await db.flush()
        await _person_with_a_phone(
            db, tenant, subject="hos", department_id=sales.id, external_id="chat-1"
        )
        mine = await _approval(db, tenant, department_id=sales.id, title="Angebot Gartenholz")
        theirs = await _approval(
            db, tenant, department_id=engineering.id, title="Produktionszugang"
        )
        mine_id, theirs_id = mine.id, theirs.id

    async with app_session(tenant) as db:
        with pytest.raises(PermissionError):
            await decision_from(
                db,
                tenant_id=tenant,
                channel_id=CHANNEL,
                external_id="chat-1",
                approval_id=theirs_id,
                verdict="approve",
            )

    async with app_session(tenant) as db:
        still = await db.get(m.ApprovalRequest, theirs_id)
        assert still is not None and still.status == "pending"

        # The control: the same phone, the same call, his own department.
        result = await decision_from(
            db,
            tenant_id=tenant,
            channel_id=CHANNEL,
            external_id="chat-1",
            approval_id=mine_id,
            verdict="approve",
        )
        assert result.approval.status == "approved"


# ----------------------------------------------------------------------- 22


async def test_a_binding_with_no_member_decides_nothing_and_says_the_same_thing_as_an_unknown_sender(  # noqa: E501
    app_session: AppSessionFactory,
) -> None:
    """The door with no password is the door that gets closed.

    Migration 0046 adds `member_id` with no backfill: a binding made before this
    slice points at nobody, and a binding that points at nobody decides nothing.
    Grandfathering it (inserting `subject = 'channel:'||user_id`) was rejected --
    it collides with `uq_org_member_subject_uuid` the first time that human
    authenticates, and 500s every request from exactly the people piloting this.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = m.Department(tenant_id=tenant, name="Vertrieb")
        db.add(sales)
        await db.flush()
        await _person_with_a_phone(
            db,
            tenant,
            subject="legacy",
            department_id=sales.id,
            external_id="chat-legacy",
            with_member=False,
        )
        approval = await _approval(db, tenant, department_id=sales.id, title="Angebot")
        approval_id = approval.id

    async with app_session(tenant) as db:
        with pytest.raises(PermissionError) as orphan:
            await decision_from(
                db,
                tenant_id=tenant,
                channel_id=CHANNEL,
                external_id="chat-legacy",
                approval_id=approval_id,
                verdict="approve",
            )
        with pytest.raises(PermissionError) as stranger:
            await decision_from(
                db,
                tenant_id=tenant,
                channel_id=CHANNEL,
                external_id="never-seen",
                approval_id=approval_id,
                verdict="approve",
            )

    assert str(orphan.value) == str(stranger.value), (
        "the bot tells an orphaned binding apart from an unknown sender, which "
        "makes it an oracle for which accounts were ever bound"
    )

    async with app_session(tenant) as db:
        row = await db.get(m.ApprovalRequest, approval_id)
        assert row is not None and row.status == "pending"

    # The link between the two doors: the uuid a binding carries is the uuid the
    # member row is found by. If these ever diverge, every binding orphans itself.
    from oc8.api.v1.channels import _subject_id

    assert _subject_uuid("legacy") == _subject_id(
        Principal(subject="legacy", tenant_id=tenant, role="member")
    )


# ----------------------------------------------------------------------- 23


async def test_announce_skips_a_binding_outside_the_department(
    app_session: AppSessionFactory,
) -> None:
    """Being told is a disclosure too. The notification carries the title and the
    amount, so a fan-out that ignores the department leaks Engineering's held
    tool call to the whole company's phones before anybody decides anything."""
    channel = FakeChannel()
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = m.Department(tenant_id=tenant, name="Vertrieb")
        engineering = m.Department(tenant_id=tenant, name="Entwicklung")
        db.add_all([sales, engineering])
        await db.flush()
        await _person_with_a_phone(
            db, tenant, subject="hos", department_id=sales.id, external_id="chat-sales"
        )
        await _person_with_a_phone(
            db, tenant, subject="cto", department_id=engineering.id, external_id="chat-eng"
        )
        # Bound, known, and standing nowhere: a member with no seat at all.
        await _person_with_a_phone(
            db, tenant, subject="newjoiner", department_id=None, external_id="chat-new"
        )
        approval = await _approval(db, tenant, department_id=sales.id, title="Angebot Gartenholz")

        await announce(db, approval, channels=_only(channel))

    assert [who for who, _notice in channel.delivered] == ["chat-sales"]

    # And the forcing trick that keeps it that way: `department_id` is a REQUIRED
    # keyword, so a caller added next year cannot fan out tenant-wide by omission.
    with pytest.raises(TypeError):
        no_session: Any = object()
        coro: Any = binding_mod.recipients(no_session, tenant_id=tenant, channel=CHANNEL)
        coro.close()
        pytest.fail("recipients() still fans out to a whole tenant when nobody says otherwise")


async def test_the_company_wide_incident_still_reaches_the_unrestricted(
    app_session: AppSessionFactory,
) -> None:
    """Not in §8's list. `department_id IS NULL` is the tenant-scope budget
    breach; if the fan-out treats NULL as "matches no seat" and stops there,
    nobody is told the company stopped working."""
    channel = FakeChannel()
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = m.Department(tenant_id=tenant, name="Vertrieb")
        db.add(sales)
        await db.flush()
        await _person_with_a_phone(
            db, tenant, subject="hos", department_id=sales.id, external_id="chat-sales"
        )
        ceo = await _person_with_a_phone(
            db, tenant, subject="ceo", department_id=None, external_id="chat-ceo"
        )
        assert ceo is not None
        ceo.all_departments = True
        await db.flush()

        incident = await _approval(db, tenant, department_id=None, title="Token budget exceeded")
        await announce(db, incident, channels=_only(channel))

    assert [who for who, _notice in channel.delivered] == ["chat-ceo"]


# ----------------------------------------------------------------------- 24


async def test_a_reason_typed_in_the_messenger_reaches_the_row(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ChannelDecision.reason` is parsed by the plugin, carried into
    `api/v1/channels.py:206-214`, and dropped on the floor there -- the one
    field `decision_from` already accepts and nobody passes.

    It matters because a rejection with no reason is a dead end for the agent
    that has to act on it, and the screen makes a reason REQUIRED to reject. The
    phone must not be the way round that.
    """
    channel = FakeChannel()

    async def _channels(_db: Any, *, tenant_id: uuid.UUID) -> dict[str, Any]:
        return {CHANNEL: channel}

    monkeypatch.setattr("oc8.api.v1.channels.channels_for_tenant", _channels)

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = m.Department(tenant_id=tenant, name="Vertrieb")
        db.add(sales)
        await db.flush()
        await _person_with_a_phone(
            db, tenant, subject="hos", department_id=sales.id, external_id="chat-1"
        )
        approval = await _approval(db, tenant, department_id=sales.id, title="Angebot Gartenholz")
        approval_id = approval.id

    channel.parsed = ChannelDecision(
        approval_id=approval_id,
        verdict="reject",
        reason="zu hoher Rabatt, bitte mit 5% neu rechnen",
        external_id="chat-1",
    )
    async with _http() as http:
        got = await http.post(f"/api/v1/channels/{CHANNEL}/webhook/{tenant}", json={"x": 1})
    assert got.status_code == 200, got.text

    async with app_session(tenant) as db:
        row = await db.get(m.ApprovalRequest, approval_id)
        assert row is not None
        assert row.status == "rejected"
        assert row.reason == "zu hoher Rabatt, bitte mit 5% neu rechnen"
