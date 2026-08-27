"""`org_member.all_departments` is company-wide decide authority, and it was
granted for ever.

Reproduced before the fix, with one subject and one endpoint:

    POST /members {"subject":"boss","allDepartments":true}   -> 201 allDepartments=true
    POST /members {"subject":"boss","allDepartments":false}  -> 200 allDepartments=true
    DELETE /members/{id}                                     -> 404 (no such route)

`upsert_member` widened and never narrowed; nothing in `src/` wrote `False`
anywhere. The 200 is the sharp edge -- an administrator reads it as done.

What it costs: `authz/scope.py` ORs the row term into `decide_everywhere` on the
HTTP path too, so the flag OUTLIVES THE ROLE THAT EARNED IT. The same person,
demoted in the identity provider from `org_admin` to `member`, kept
`viewsAllDepartments: true`, kept seeing every department's approvals, and kept
deciding them.

The design calls the grant "durable, audited, and revocable" (§2) and
`models/identity.py` repeats "revocable". These are the tests that make the third
word true.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.authz.permissions import MEMBER_ROLE, ORG_ADMIN
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID, subject: str, role: str) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=role)
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def test_false_actually_revokes_it_and_says_so_in_the_trail(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    admin = _headers(tenant, "admin", ORG_ADMIN)

    async with _http() as http:
        granted = await http.post(
            "/api/v1/members",
            json={"subject": "boss", "displayName": "Boss", "allDepartments": True},
            headers=admin,
        )
        assert granted.status_code == 201, granted.text
        assert granted.json()["allDepartments"] is True

        revoked = await http.post(
            "/api/v1/members",
            json={"subject": "boss", "allDepartments": False},
            headers=admin,
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["allDepartments"] is False, (
            "the endpoint answered 200 and changed nothing -- an administrator "
            "reads that as the authority having been taken away"
        )

    async with app_session(tenant) as db:
        row = (
            await db.execute(select(m.OrgMember).where(m.OrgMember.subject == "boss"))
        ).scalar_one()
        assert row.all_departments is False
        # And the ROW, not only the response: a DTO built from the request body
        # would satisfy the assertion above while the database still said true.

        events = (
            (
                await db.execute(
                    select(m.AuditEvent)
                    .where(m.AuditEvent.category == "member")
                    .order_by(m.AuditEvent.seq)
                )
            )
            .scalars()
            .all()
        )
        actions = [e.action for e in events]
        assert actions == [
            "member.all_departments_granted",
            "member.all_departments_revoked",
        ], actions
        assert events[-1].resource["subject"] == "boss"


async def test_omitting_the_field_leaves_the_grant_alone(app_session: AppSessionFactory) -> None:
    """The symmetric mistake, and the reason the field is tri-state rather than a
    `bool` defaulting to `False`: an administrator correcting somebody's display
    name must not strip a CEO of his company-wide view as a side effect.

    `display_name` is asserted too, so "the whole body was ignored" cannot pass
    this.
    """
    tenant = uuid.uuid4()
    admin = _headers(tenant, "admin", ORG_ADMIN)

    async with _http() as http:
        await http.post(
            "/api/v1/members",
            json={"subject": "boss", "allDepartments": True},
            headers=admin,
        )
        touched = await http.post(
            "/api/v1/members",
            json={"subject": "boss", "displayName": "Frau Krüger"},
            headers=admin,
        )
        assert touched.status_code == 200, touched.text
        assert touched.json()["allDepartments"] is True
        assert touched.json()["displayName"] == "Frau Krüger"

    async with app_session(tenant) as db:
        events = (
            (await db.execute(select(m.AuditEvent).where(m.AuditEvent.category == "member")))
            .scalars()
            .all()
        )
        assert [e.action for e in events] == ["member.all_departments_granted"], (
            "a request that said nothing about the flag wrote an audit line "
            "claiming somebody asserted it"
        )


async def test_a_demoted_holder_stops_seeing_and_deciding_on_the_next_request(
    app_session: AppSessionFactory,
) -> None:
    """The whole point, end to end, and the shape the reviewers reproduced.

    The subject keeps its token role `member` throughout -- i.e. the person has
    ALREADY been demoted in the identity provider and the row is the only thing
    still granting anything. Revoking it must take effect on the very next
    request, with no token refresh, exactly as revoking a seat does.
    """
    tenant = uuid.uuid4()
    admin = _headers(tenant, "admin", ORG_ADMIN)
    demoted = _headers(tenant, "boss", MEMBER_ROLE)

    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Entwicklung")
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Ada")
        db.add(agent)
        await db.flush()
        held = m.ApprovalRequest(
            tenant_id=tenant,
            agent_id=agent.id,
            department_id=dept.id,
            action_type="tool_send",
            status="pending",
            title="ENG SECRET",
            detail="",
            payload={"tool": "prod.deploy", "arguments": {}},
        )
        db.add(held)
        await db.flush()
        held_id = held.id

    async with _http() as http:
        await http.post(
            "/api/v1/members",
            json={"subject": "boss", "allDepartments": True},
            headers=admin,
        )
        # Holding the row grant and nothing else, he sees Engineering.
        assert [
            r["title"] for r in (await http.get("/api/v1/approvals", headers=demoted)).json()
        ] == ["ENG SECRET"]
        me = (await http.get("/api/v1/me", headers=demoted)).json()
        assert me["role"] == MEMBER_ROLE
        assert me["viewsAllDepartments"] is True
        assert me["decidesAllDepartments"] is True
        assert me["seats"] == []

        await http.post(
            "/api/v1/members",
            json={"subject": "boss", "allDepartments": False},
            headers=admin,
        )

        # The very next request. No refresh, no logout, no TTL.
        assert (await http.get("/api/v1/approvals", headers=demoted)).status_code == 403
        me_after = (await http.get("/api/v1/me", headers=demoted)).json()
        assert me_after["viewsAllDepartments"] is False
        assert me_after["decidesAllDepartments"] is False

        refused = await http.post(
            f"/api/v1/approvals/{held_id}/decision",
            json={"decision": "approve"},
            headers=demoted,
        )
        assert refused.status_code == 403, refused.text

    async with app_session(tenant) as db:
        row = await db.get(m.ApprovalRequest, held_id)
        assert row is not None and row.status == "pending"


async def test_the_phone_narrows_with_the_row(app_session: AppSessionFactory) -> None:
    """`scope_for_binding` reads `all_departments` live off the row, so the
    messenger door -- which has no token and no other unrestricted term -- must
    narrow the moment it is revoked. If it did not, revoking would take the HTTP
    authority away and leave the one that answers from a phone at 23:40.
    """
    from oc8.authz.scope import scope_for_binding, subject_uuid_for

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        member = m.OrgMember(
            tenant_id=tenant,
            subject="boss",
            subject_uuid=subject_uuid_for("boss"),
            display_name="Boss",
            all_departments=True,
        )
        db.add(member)
        await db.flush()
        binding = m.ApprovalChannelBinding(
            tenant_id=tenant,
            channel="fake",
            user_id=member.subject_uuid,
            external_id="chat-boss",
            member_id=member.id,
        )
        db.add(binding)
        await db.flush()

        _who, scope = await scope_for_binding(db, binding)
        assert scope.is_unrestricted is True
        assert scope.decides_everywhere is True

    async with _http() as http:
        await http.post(
            "/api/v1/members",
            json={"subject": "boss", "allDepartments": False},
            headers=_headers(tenant, "admin", ORG_ADMIN),
        )

    async with app_session(tenant) as db:
        binding = (
            await db.execute(
                select(m.ApprovalChannelBinding).where(
                    m.ApprovalChannelBinding.external_id == "chat-boss"
                )
            )
        ).scalar_one()
        _who, scope = await scope_for_binding(db, binding)
        assert scope.is_unrestricted is False
        assert scope.is_empty is True
