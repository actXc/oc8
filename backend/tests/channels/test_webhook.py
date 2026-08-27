"""The inbound webhook — the one route in oc8 that no oc8 token protects.

It is called by Telegram or Meta, not by a user, so authentication has to come
from the platform's own signature. Everything here is about what happens when
that is missing, wrong, or forged.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.channels import ChannelCapabilities, ChannelDecision, ChannelLink
from oc8.channels.binding import issue_code
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

CHANNEL = "fake"


@dataclass
class FakeChannel:
    channel_id: str = CHANNEL
    verifies: bool = True
    parsed: Any = None
    said: list[tuple[str, str]] = field(default_factory=list)

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(max_classification="internal")

    def verify_inbound(self, *, headers: dict[str, str], body: bytes) -> bool:
        return self.verifies

    def parse_inbound(self, update: dict[str, Any]) -> Any:
        return self.parsed

    async def deliver(self, notice: Any, *, external_id: str) -> str | None:
        return "handle"

    async def withdraw(self, *a: Any, **k: Any) -> None:
        return None

    async def say(self, external_id: str, text: str) -> None:
        self.said.append((external_id, text))


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    """The webhook carries no oc8 token, so no auth header is set anywhere in
    this file — that is the property under test, not an omission."""
    from oc8.main import create_app

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


async def _approval(db: Any, tenant: uuid.UUID) -> m.ApprovalRequest:
    ar = m.ApprovalRequest(
        tenant_id=tenant,
        agent_id=uuid.uuid4(),
        action_type="decision",
        payload={"options": [{"key": "full", "label": "Voll"}]},
        status="pending",
        title="Erstattung freigeben?",
        detail="249 EUR",
    )
    db.add(ar)
    await db.flush()
    return ar


async def _bind(db: Any, tenant: uuid.UUID, external_id: str) -> None:
    """Bind an account to a person who stands in every department.

    A binding that names nobody decides nothing since slice 1 -- `member_id` is
    what `dispatch.decision_from` resolves a scope from -- and the approvals in
    this file carry no department, which only the unrestricted may answer. The
    department term itself is tested in `test_channel_decision_is_scoped.py`;
    what these tests are about is the webhook's own behaviour around it.
    """
    from oc8.authz.scope import subject_uuid_for
    from oc8.channels.binding import redeem_code

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


@pytest.fixture
def queue(redis_url: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A decision can start a follow-up run, and enqueueing it is part of what
    the webhook does — stubbing it out would test a different thing."""
    from oc8.runtime.queue import RunQueue

    q = RunQueue(redis_url, key=f"test:{uuid.uuid4()}")
    monkeypatch.setattr("oc8.runtime.intake.get_run_queue", lambda: q)
    return q


@pytest.fixture
def channel(monkeypatch: pytest.MonkeyPatch) -> FakeChannel:
    impl = FakeChannel()

    async def _channels(_db: Any, *, tenant_id: uuid.UUID) -> dict[str, Any]:
        return {CHANNEL: impl}

    monkeypatch.setattr("oc8.api.v1.channels.channels_for_tenant", _channels)
    return impl


async def test_an_unsigned_call_is_refused_before_anything_is_parsed(
    app_session: AppSessionFactory, channel: FakeChannel
) -> None:
    """The check runs first and the payload is never looked at. An
    unauthenticated webhook that reaches the decision path lets anyone who
    learns the URL approve anything — and the URL is not a secret."""
    tenant = uuid.uuid4()
    channel.verifies = False
    channel.parsed = ChannelDecision(
        approval_id=uuid.uuid4(), verdict="approve", external_id="acct"
    )
    async with _http() as http:
        got = await http.post(f"/api/v1/channels/{CHANNEL}/webhook/{tenant}", json={"x": 1})
    assert got.status_code == 401
    assert channel.said == [], "a refused call gets no reply either"


async def test_an_unknown_channel_and_a_wrong_tenant_look_identical(
    app_session: AppSessionFactory, channel: FakeChannel
) -> None:
    async with _http() as http:
        got = await http.post(f"/api/v1/channels/nope/webhook/{uuid.uuid4()}", json={})
    assert got.status_code == 404


async def test_a_signed_call_from_an_unbound_sender_decides_nothing(
    app_session: AppSessionFactory, channel: FakeChannel
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        approval = await _approval(db, tenant)
        approval_id = approval.id
    channel.parsed = ChannelDecision(
        approval_id=approval_id, verdict="approve", external_id="stranger"
    )

    async with _http() as http:
        got = await http.post(f"/api/v1/channels/{CHANNEL}/webhook/{tenant}", json={})
    assert got.status_code == 200, "200 so the platform does not disable the webhook"

    async with app_session(tenant) as db:
        fresh = await db.get(m.ApprovalRequest, approval_id)
        assert fresh is not None and fresh.status == "pending"
    assert channel.said and "nicht berechtigt" in channel.said[0][1]


async def test_a_bound_sender_decides_and_the_approval_moves(
    app_session: AppSessionFactory, channel: FakeChannel, queue: Any
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        approval = await _approval(db, tenant)
        approval_id = approval.id
        await _bind(db, tenant, "acct-1")

    channel.parsed = ChannelDecision(
        approval_id=approval_id, verdict="approve", option_key="full", external_id="acct-1"
    )
    async with _http() as http:
        got = await http.post(f"/api/v1/channels/{CHANNEL}/webhook/{tenant}", json={})
    assert got.status_code == 200

    async with app_session(tenant) as db:
        fresh = await db.get(m.ApprovalRequest, approval_id)
        assert fresh is not None and fresh.status == "approved"


async def test_answering_twice_is_told_what_happened_not_that_it_failed(
    app_session: AppSessionFactory, channel: FakeChannel
) -> None:
    """Several people can have the same request open. Whoever is slower is not
    doing anything wrong and deserves a sentence, not a refusal."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        approval = await _approval(db, tenant)
        approval.status = "approved"
        approval_id = approval.id
        await _bind(db, tenant, "acct-1")

    channel.parsed = ChannelDecision(
        approval_id=approval_id, verdict="reject", external_id="acct-1"
    )
    async with _http() as http:
        got = await http.post(f"/api/v1/channels/{CHANNEL}/webhook/{tenant}", json={})
    assert got.status_code == 200
    assert channel.said and "Schon entschieden" in channel.said[0][1]


async def test_a_code_sent_to_the_bot_binds_that_account(
    app_session: AppSessionFactory, channel: FakeChannel
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        row = await issue_code(db, tenant_id=tenant, user_id=uuid.uuid4(), channel=CHANNEL)
        assert row.code is not None
        code = row.code

    channel.parsed = ChannelLink(code=code, external_id="acct-9")
    async with _http() as http:
        got = await http.post(f"/api/v1/channels/{CHANNEL}/webhook/{tenant}", json={})
    assert got.status_code == 200
    assert channel.said and "Verbunden" in channel.said[0][1]

    async with app_session(tenant) as db:
        from oc8.channels.binding import resolve

        assert await resolve(db, tenant_id=tenant, channel=CHANNEL, external_id="acct-9")


async def test_a_bad_code_says_the_same_thing_every_time(
    app_session: AppSessionFactory, channel: FakeChannel
) -> None:
    tenant = uuid.uuid4()
    channel.parsed = ChannelLink(code="never-issued", external_id="acct-9")
    async with _http() as http:
        got = await http.post(f"/api/v1/channels/{CHANNEL}/webhook/{tenant}", json={})
    assert got.status_code == 200
    assert channel.said and channel.said[0][1] == "Dieser Code gilt nicht."


async def test_anything_the_plugin_does_not_recognise_is_simply_acknowledged(
    app_session: AppSessionFactory, channel: FakeChannel
) -> None:
    """Somebody typing at the bot, a delivery receipt, the platform's own
    housekeeping. A non-200 here makes Telegram retry and Meta disable the
    webhook, so an error would cost the channel itself."""
    channel.parsed = None
    async with _http() as http:
        got = await http.post(f"/api/v1/channels/{CHANNEL}/webhook/{uuid.uuid4()}", json={})
    assert got.status_code == 200
    assert channel.said == []
