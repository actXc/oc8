"""A viewer seat on a phone: told, and not able to answer.

Not in §8's list. §8.21 pins the DEPARTMENT at the messenger door (a Sales
approver may not decide Engineering's approval); this pins the SEAT ROLE inside
one department, which is the other half of `SEAT_PERMISSIONS` and the only reason
`dept_viewer` exists as a separate word.

It is also where the two checks in `dispatch.decision_from` differ:
`load_for_actor` answers may_VIEW, so a viewer gets past it with the row in hand,
and only the explicit `may_decide` after it refuses him. Delete that line and
every §8 test still passes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from oc8 import models as m
from oc8.authz.permissions import SEAT_APPROVER, SEAT_VIEWER
from oc8.authz.scope import subject_uuid_for
from oc8.channels import ChannelCapabilities
from oc8.channels.base import ApprovalChannel
from oc8.channels.dispatch import announce, decision_from
from oc8.channels.notice import ApprovalNotice
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

CHANNEL = "fake"


@dataclass
class FakeChannel:
    channel_id: str = CHANNEL
    delivered: list[str] = field(default_factory=list)

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(max_classification="internal")

    async def deliver(self, notice: ApprovalNotice, *, external_id: str) -> str | None:
        self.delivered.append(external_id)
        return f"msg-{external_id}"

    async def withdraw(self, *a: Any, **k: Any) -> None:
        return None


async def _seated_phone(
    db: Any, tenant: uuid.UUID, *, subject: str, department_id: uuid.UUID, seat_role: str
) -> None:
    member = m.OrgMember(tenant_id=tenant, subject=subject, subject_uuid=subject_uuid_for(subject))
    db.add(member)
    await db.flush()
    db.add(
        m.OrgMemberDepartment(
            tenant_id=tenant,
            member_id=member.id,
            department_id=department_id,
            seat_role=seat_role,
        )
    )
    db.add(
        m.ApprovalChannelBinding(
            tenant_id=tenant,
            channel=CHANNEL,
            user_id=member.subject_uuid,
            external_id=f"chat-{subject}",
            member_id=member.id,
        )
    )
    await db.flush()


async def test_a_viewer_is_told_and_still_cannot_answer(
    app_session: AppSessionFactory,
) -> None:
    channel = FakeChannel()
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = m.Department(tenant_id=tenant, name="Vertrieb")
        db.add(sales)
        await db.flush()
        await _seated_phone(
            db, tenant, subject="watcher", department_id=sales.id, seat_role=SEAT_VIEWER
        )
        await _seated_phone(
            db, tenant, subject="hos", department_id=sales.id, seat_role=SEAT_APPROVER
        )
        agent = m.Agent(tenant_id=tenant, department_id=sales.id, name="Nora")
        db.add(agent)
        await db.flush()
        approval = m.ApprovalRequest(
            tenant_id=tenant,
            agent_id=agent.id,
            department_id=sales.id,
            action_type="tool_send",
            status="pending",
            title="Angebot Gartenholz GmbH",
            detail="",
            amount_text="4.320,00 EUR",
        )
        db.add(approval)
        await db.flush()
        approval_id = approval.id

        await announce(db, approval, channels={CHANNEL: cast(ApprovalChannel, channel)})

    assert sorted(channel.delivered) == ["chat-hos", "chat-watcher"], (
        "a dept_viewer holds approval:view and is part of the department's "
        "audience; dropping him would make the seat role mean 'nothing'"
    )

    async with app_session(tenant) as db:
        with pytest.raises(PermissionError) as viewer:
            await decision_from(
                db,
                tenant_id=tenant,
                channel_id=CHANNEL,
                external_id="chat-watcher",
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
    assert str(viewer.value) == str(stranger.value), (
        "the refusal tells a viewer apart from a stranger, which confirms both "
        "that the account is bound and that the approval is real"
    )

    async with app_session(tenant) as db:
        row = await db.get(m.ApprovalRequest, approval_id)
        assert row is not None and row.status == "pending"
        assert row.decided_by is None

        # The control: the approver seat in the same department, same call.
        result = await decision_from(
            db,
            tenant_id=tenant,
            channel_id=CHANNEL,
            external_id="chat-hos",
            approval_id=approval_id,
            verdict="approve",
        )
        assert result.approval.status == "approved"
