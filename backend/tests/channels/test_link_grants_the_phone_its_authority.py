"""Where a messenger gets its authority, and why it can only be here.

The webhook carries no oc8 token, so `approval:decide_any` -- the claim that
makes a CEO unrestricted -- is unreadable at the door where the decision arrives.
The asymmetry is closed at LINK time instead: `POST /channels/{channel}/link` is
already authenticated and already `channel:manage`, so the binding is issued
naming a person, and a caller who holds `decide_any` has `all_departments`
written on that person once, durably, revocably and audited.

Not in §8's list. It is here because everything §8 does test about the messenger
door assumes a binding that names somebody -- and this route is the only thing in
the system that produces one.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.auth.principal import Principal
from oc8.authz.permissions import SEAT_APPROVER
from oc8.authz.scope import subject_uuid_for
from oc8.channels.dispatch import decision_from
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

CHANNEL = "fake"


class _FakeChannel:
    channel_id = CHANNEL

    def capabilities(self) -> Any:
        from oc8.channels import ChannelCapabilities

        return ChannelCapabilities(max_classification="internal")

    async def verify_inbound(self, *, headers: dict[str, str], body: bytes) -> bool:
        return True

    def parse_inbound(self, update: dict[str, Any]) -> Any:
        return None

    async def deliver(self, notice: Any, *, external_id: str) -> str | None:
        return "handle"

    async def withdraw(self, *a: Any, **k: Any) -> None:
        return None


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


@pytest.fixture(autouse=True)
def _channel(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _channels(_db: Any, *, tenant_id: uuid.UUID) -> dict[str, Any]:
        return {CHANNEL: _FakeChannel()}

    monkeypatch.setattr("oc8.api.v1.channels.channels_for_tenant", _channels)


async def _link(tenant: uuid.UUID, subject: str, role: str) -> int:
    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=role)
    async with _http() as http:
        got = await http.post(
            f"/api/v1/channels/{CHANNEL}/link", headers={"Authorization": f"Bearer {token}"}
        )
    return got.status_code


async def _person(db: Any, subject: str) -> m.OrgMember:
    row = (await db.execute(select(m.OrgMember).where(m.OrgMember.subject == subject))).scalar_one()
    return cast(m.OrgMember, row)


async def test_linking_names_the_person_the_phone_speaks_for(
    app_session: AppSessionFactory,
) -> None:
    """A binding with `member_id IS NULL` decides nothing, so a link route that
    forgot to write it would produce phones that are told nothing and can answer
    nothing -- and the only symptom would be a bot that has gone quiet."""
    tenant = uuid.uuid4()
    assert await _link(tenant, "boss", "org_admin") == 200

    async with app_session(tenant) as db:
        member = await _person(db, "boss")
        binding = (await db.execute(select(m.ApprovalChannelBinding))).scalar_one()
        assert binding.member_id == member.id
        assert binding.user_id == member.subject_uuid, (
            "the uuid the binding is filed under is not the one the member is "
            "found by; every binding in the tenant would orphan itself"
        )


async def test_a_linker_who_cannot_decide_everywhere_does_not_become_able_to(
    app_session: AppSessionFactory,
) -> None:
    """The route body is called directly, and that is deliberate.

    `channel:manage` is held by `org_admin` and by nobody else today
    (`_DEPT_MANAGER` does not carry it), and `org_admin` holds
    `approval:decide_any` through ALL_PERMISSIONS -- so through HTTP there is
    currently no caller who can reach this route WITHOUT the claim, and the
    condition would be untested until the day a tenant-defined role has one and
    not the other. That day is slice 2. Calling the body with an `operator`
    principal asks the only question this test is about: is the grant conditional
    on the permission, or on having reached the route at all?
    """
    from oc8.api.v1.channels import create_link_code

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        # This commits (the route does), which unbinds `app.tenant_id` for the
        # rest of THIS session -- hence the fresh one below rather than asserting
        # here on rows that would silently come back empty.
        await create_link_code(
            CHANNEL, db, Principal(subject="op-1", tenant_id=tenant, role="operator")
        )

    async with app_session(tenant) as db:
        member = await _person(db, "op-1")
        assert member.all_departments is False, (
            "linking a phone made somebody unrestricted who does not hold "
            "approval:decide_any -- the grant is on reaching the route, not on "
            "the claim"
        )
        binding = (await db.execute(select(m.ApprovalChannelBinding))).scalar_one()
        assert binding.member_id == member.id, "the binding still names its person"
        assert (
            await db.execute(
                select(m.AuditEvent).where(m.AuditEvent.action == "member.all_departments_granted")
            )
        ).scalars().all() == [], "an audit event for a grant that did not happen"


async def test_the_holder_of_decide_any_carries_it_onto_the_phone(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    assert await _link(tenant, "boss", "org_admin") == 200

    async with app_session(tenant) as db:
        member = await _person(db, "boss")
        assert member.all_departments is True

        event = (
            await db.execute(
                select(m.AuditEvent).where(m.AuditEvent.action == "member.all_departments_granted")
            )
        ).scalar_one()
        assert event.resource["member_id"] == str(member.id)
        assert event.actor_id == member.id


async def test_the_grant_is_what_lets_a_phone_reach_a_department_it_has_no_seat_in(
    app_session: AppSessionFactory,
) -> None:
    """The end of the chain, and the reason the row term exists at all.

    The messenger door has no token, so `all_departments` on the row is the ONLY
    unrestricted term available there. A phone that holds it decides in a
    department its owner has no seat in; a phone that does not is refused with the
    flat sentence every refusal at this door shares.

    Asserted on a `tool_send` approval and no longer on the tenant-wide budget
    incident, because those are now two different questions. The budget incident
    is still the only thing that carries `department_id IS NULL`, and the
    scope-half of this test still runs against it below -- but approving one
    APPLIES a `budget:manage` effect (`resolve_budget_incident` sets
    `override_until` and resumes the frozen scope), and a `ChannelActor` holds no
    tenant-wide permission at all because there is no token to read one from. So
    the phone gets as far as "yes, this is yours" and is then refused by the
    effect gate, with the same sentence, and the boss opens the app for that one.
    See `approvals/service.py::EFFECT_PERMISSIONS`: a scope says WHERE, never
    WHAT.
    """
    tenant = uuid.uuid4()
    assert await _link(tenant, "boss", "org_admin") == 200

    async with app_session(tenant) as db:
        boss = await _person(db, "boss")
        binding = (
            await db.execute(
                select(m.ApprovalChannelBinding).where(
                    m.ApprovalChannelBinding.member_id == boss.id
                )
            )
        ).scalar_one()
        binding.external_id = "chat-boss"
        binding.code = None

        # The control, built by hand because no seat-holding role can reach the
        # link route today: a real person, a real live seat, a real bound phone --
        # and no `all_departments`.
        sales = m.Department(tenant_id=tenant, name="Vertrieb")
        db.add(sales)
        await db.flush()
        seated = m.OrgMember(
            tenant_id=tenant,
            subject="hos",
            subject_uuid=subject_uuid_for("hos"),
            display_name="Head of Sales",
        )
        db.add(seated)
        await db.flush()
        db.add(
            m.OrgMemberDepartment(
                tenant_id=tenant,
                member_id=seated.id,
                department_id=sales.id,
                seat_role=SEAT_APPROVER,
            )
        )
        db.add(
            m.ApprovalChannelBinding(
                tenant_id=tenant,
                channel=CHANNEL,
                user_id=seated.subject_uuid,
                external_id="chat-hos",
                member_id=seated.id,
            )
        )
        agent = m.Agent(tenant_id=tenant, department_id=sales.id, name="Nora")
        db.add(agent)
        await db.flush()
        incident = m.ApprovalRequest(
            tenant_id=tenant,
            agent_id=agent.id,
            department_id=None,
            action_type="budget_incident",
            status="pending",
            title="Token-Budget überschritten",
            detail="",
        )
        db.add(incident)
        # An ordinary held tool call in ENGINEERING -- a department the Head of
        # Sales holds no seat in and the boss holds no seat in either. Only the row
        # term can reach it.
        engineering = m.Department(tenant_id=tenant, name="Entwicklung")
        db.add(engineering)
        await db.flush()
        ada = m.Agent(tenant_id=tenant, department_id=engineering.id, name="Ada")
        db.add(ada)
        await db.flush()
        held = m.ApprovalRequest(
            tenant_id=tenant,
            agent_id=ada.id,
            department_id=engineering.id,
            action_type="tool_send",
            status="pending",
            title="Produktionszugang",
            detail="",
        )
        db.add(held)
        await db.flush()
        incident_id = incident.id
        held_id = held.id

    async with app_session(tenant) as db:
        with pytest.raises(PermissionError):
            await decision_from(
                db,
                tenant_id=tenant,
                channel_id=CHANNEL,
                external_id="chat-hos",
                approval_id=incident_id,
                verdict="approve",
            )
        # Same flat refusal for the same phone against Engineering's held call:
        # the seat is in Vertrieb and this is not Vertrieb.
        with pytest.raises(PermissionError):
            await decision_from(
                db,
                tenant_id=tenant,
                channel_id=CHANNEL,
                external_id="chat-hos",
                approval_id=held_id,
                verdict="approve",
            )

    async with app_session(tenant) as db:
        # The grant IS what carries the boss into a department he has no seat in.
        result = await decision_from(
            db,
            tenant_id=tenant,
            channel_id=CHANNEL,
            external_id="chat-boss",
            approval_id=held_id,
            verdict="approve",
        )
        assert result.approval.status == "approved"

    async with app_session(tenant) as db:
        # And it is NOT a management right. The scope admits him to the
        # tenant-wide row -- `may_decide(None)` is true for `all_departments` and
        # for nobody else -- and the effect gate then refuses it, because
        # approving lifts the token cap for the month and no phone carries
        # `budget:manage`. Indistinguishable from every other refusal here on
        # purpose; the bot must not become a directory of which approvals exist.
        with pytest.raises(PermissionError):
            await decision_from(
                db,
                tenant_id=tenant,
                channel_id=CHANNEL,
                external_id="chat-boss",
                approval_id=incident_id,
                verdict="approve",
            )
        still = (
            await db.execute(select(m.ApprovalRequest).where(m.ApprovalRequest.id == incident_id))
        ).scalar_one()
        assert still.status == "pending"
