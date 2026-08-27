"""Who signed it off.

`ApprovalRequest.decided_by` is declared at `models/ops.py:67` and assigned
nowhere in `src/` -- the column has existed since 0001 and has never held a
value. That is not a cosmetic gap: "a second person signs off" is the entire
value of an approval gate (§5.5), and until this slice there was no row for a
person to point at, so the field could not have been filled.

Both doors have to fill it, and the messenger door is the one that matters most:
a decision made from a phone at 23:40 currently records `actor_id=None` in the
audit line, so the trail says an operator decided and cannot say which one.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.authz.permissions import SEAT_APPROVER
from oc8.channels.dispatch import decision_from
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

CHANNEL = "fake"


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


def _subject_uuid(subject: str) -> uuid.UUID:
    try:
        return uuid.UUID(subject)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"oc8:subject:{subject}")


async def _desk(db: Any, tenant: uuid.UUID, subject: str) -> tuple[m.OrgMember, uuid.UUID]:
    """One department, one seated approver, one pending approval in it."""
    department = m.Department(tenant_id=tenant, name="Vertrieb")
    db.add(department)
    await db.flush()
    agent = m.Agent(tenant_id=tenant, department_id=department.id, name="Nora")
    db.add(agent)
    await db.flush()
    member = m.OrgMember(
        tenant_id=tenant,
        subject=subject,
        subject_uuid=_subject_uuid(subject),
        display_name="Head of Sales",
    )
    db.add(member)
    await db.flush()
    db.add(
        m.OrgMemberDepartment(
            tenant_id=tenant,
            member_id=member.id,
            department_id=department.id,
            seat_role=SEAT_APPROVER,
        )
    )
    approval = m.ApprovalRequest(
        tenant_id=tenant,
        agent_id=agent.id,
        department_id=department.id,
        action_type="tool_send",
        status="pending",
        title="Angebot Gartenholz GmbH",
        detail="",
        amount_text="4.320,00 EUR",
        payload={"tool": "odoo.send_quotation", "arguments": {}},
    )
    db.add(approval)
    await db.flush()
    return member, approval.id


async def test_both_doors_name_the_member_who_decided(
    app_session: AppSessionFactory,
) -> None:
    # ---------------------------------------------------------- the inbox door
    inbox_tenant = uuid.uuid4()
    async with app_session(inbox_tenant) as db:
        inbox_member, inbox_approval = await _desk(db, inbox_tenant, "hos")
        inbox_member_id = inbox_member.id

    token = get_identity_provider().mint(tenant_id=inbox_tenant, subject="hos", role="member")
    async with _http() as http:
        got = await http.post(
            f"/api/v1/approvals/{inbox_approval}/decision",
            json={"decision": "approve"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert got.status_code == 200, got.text

    async with app_session(inbox_tenant) as db:
        row = await db.get(m.ApprovalRequest, inbox_approval)
        assert row is not None
        assert row.decided_by == inbox_member_id, (
            "the inbox recorded a decision with no person behind it"
        )

    # ------------------------------------------------------- the messenger door
    chat_tenant = uuid.uuid4()
    async with app_session(chat_tenant) as db:
        chat_member, chat_approval = await _desk(db, chat_tenant, "hos-phone")
        chat_member_id = chat_member.id
        db.add(
            m.ApprovalChannelBinding(
                tenant_id=chat_tenant,
                channel=CHANNEL,
                user_id=chat_member.subject_uuid,
                external_id="chat-1",
                member_id=chat_member.id,
            )
        )
        await db.flush()

    async with app_session(chat_tenant) as db:
        await decision_from(
            db,
            tenant_id=chat_tenant,
            channel_id=CHANNEL,
            external_id="chat-1",
            approval_id=chat_approval,
            verdict="approve",
        )

    async with app_session(chat_tenant) as db:
        row = await db.get(m.ApprovalRequest, chat_approval)
        assert row is not None
        assert row.status == "approved"
        assert row.decided_by == chat_member_id, (
            "a decision from a phone is attributable or it is not an approval"
        )

        event = (
            await db.execute(select(m.AuditEvent).where(m.AuditEvent.category == "approval"))
        ).scalar_one()
        assert event.actor_id == chat_member_id, (
            "the audit line still says 'an operator', which is the one thing an "
            "audit of a 23:40 phone approval cannot use"
        )
        assert event.resource["department_id"] == str(row.department_id)
        assert event.resource["member_id"] == str(chat_member_id)
        assert event.resource["via"] == CHANNEL
