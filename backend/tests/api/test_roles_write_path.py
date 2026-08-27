"""What the role WRITES leave behind, and the two shapes that can lose half of one.

`test_roles_endpoint.py` proves the feature exists: a role is composed, assigned,
and reaches the doors it names. This file is about the transactions underneath
it, and about the trail afterwards -- the two things that are invisible from a
status code and that this repository has already been wrong about twice.

* **`PUT /roles/{id}` is a DELETE and then INSERTs.** A commit between the two
  unbinds `app.tenant_id`, so the INSERTs match no RLS policy, affect zero rows,
  and the response reports the set it meant to write. The role comes out holding
  NOTHING and every holder is silently narrowed to nothing.
* **`POST /members/roles:bulk` is forty writes.** Half a batch is a result
  nobody can state: the caller is told 200 and has no way to know which of his
  forty rows landed.
* **Every write is audited.** "When did Anna get this, and who did it" is the
  first question asked about an authority change, and a role edit that changes
  forty people's authority with no diff in the trail answers it with the shrug
  the audit exists to prevent.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
import sqlalchemy as sa
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.authz.permissions import (
    AGENT_DEFAULT,
    APPROVAL,
    APPROVAL_DECIDE,
    AUDIT,
    CLARIFICATION_VIEW,
    ORG_ADMIN,
    VIEW,
    perm,
)
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

APPROVAL_VIEW = perm(APPROVAL, VIEW)
AUDIT_VIEW = perm(AUDIT, VIEW)
FREIGABE = [APPROVAL_VIEW, APPROVAL_DECIDE, CLARIFICATION_VIEW]


def _headers(tenant: uuid.UUID, subject: str = "boss", role: str = ORG_ADMIN) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=role)
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def _role(http: AsyncClient, tenant: uuid.UUID, name: str, permissions: list[str]) -> str:
    created = await http.post(
        "/api/v1/roles",
        json={"name": name, "description": "", "permissions": permissions},
        headers=_headers(tenant),
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def _member(http: AsyncClient, tenant: uuid.UUID, subject: str) -> str:
    minted = await http.post(
        "/api/v1/members",
        json={"subject": subject, "displayName": subject.title()},
        headers=_headers(tenant),
    )
    assert minted.status_code in (200, 201), minted.text
    return str(minted.json()["id"])


async def _grants(app_session: AppSessionFactory, tenant: uuid.UUID, role_id: str) -> set[str]:
    async with app_session(tenant) as db:
        rows = (
            await db.execute(
                sa.select(m.RolePermission.permission).where(
                    m.RolePermission.tenant_id == tenant,
                    m.RolePermission.role_id == uuid.UUID(role_id),
                )
            )
        ).scalars()
        return set(rows)


async def _events(app_session: AppSessionFactory, tenant: uuid.UUID) -> list[Any]:
    async with app_session(tenant) as db:
        return list(
            (
                await db.execute(
                    sa.select(m.AuditEvent)
                    .where(m.AuditEvent.tenant_id == tenant)
                    .order_by(m.AuditEvent.seq)
                )
            )
            .scalars()
            .all()
        )


# ------------------------------------------------------- the transaction shapes


async def test_a_replacement_writes_the_whole_new_set_and_not_half_of_it(
    app_session: AppSessionFactory,
) -> None:
    """The rows, read back from the table rather than from the response.

    The response is built in the same transaction as the write, so it would
    happily report a set that never landed: with a commit between the DELETE and
    the INSERTs, `app.tenant_id` is unbound and the inserts affect zero rows,
    while the handler's own re-read sees its pending objects. Reading the table
    in a NEW session is what tells the two apart.

    Three edits, because the failure is not symmetrical: widening leaves the old
    rows behind if the DELETE is skipped, narrowing leaves them behind if the
    DELETE is scoped wrongly, and replacing outright is the only one that catches
    both at once.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        role_id = await _role(http, tenant, "Abteilungsleitung", FREIGABE)
        assert await _grants(app_session, tenant, role_id) == set(FREIGABE)

        for wanted in (
            [*FREIGABE, AUDIT_VIEW],
            [APPROVAL_VIEW],
            [CLARIFICATION_VIEW, AUDIT_VIEW],
        ):
            edited = await http.put(
                f"/api/v1/roles/{role_id}",
                json={"description": "Leitet eine Abteilung", "permissions": wanted},
                headers=_headers(tenant),
            )
            assert edited.status_code < 300, edited.text
            assert sorted(edited.json()["permissions"]) == sorted(wanted)
            assert await _grants(app_session, tenant, role_id) == set(wanted), (
                f"the table does not hold what the response promised for {wanted}"
            )


async def test_a_bulk_assignment_is_all_or_nothing(
    app_session: AppSessionFactory,
) -> None:
    """One unknown id in the middle, and NOBODY is assigned.

    Deliberately in the middle: at the end, a loop that committed per person
    would already have written everybody before it; at the start, nothing would
    have been written under any shape. Only "one transaction, one commit at the
    end" leaves zero rows, and that is what makes the 404 an answer the caller
    can act on -- fix the row, send it again -- rather than a state he has to
    reconstruct.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        role_id = await _role(http, tenant, "Freigabe", FREIGABE)
        ids = [await _member(http, tenant, s) for s in ("erste", "zweite", "dritte")]

        refused = await http.post(
            "/api/v1/members/roles:bulk",
            json={"memberIds": [ids[0], str(uuid.uuid4()), ids[2]], "roleId": role_id},
            headers=_headers(tenant),
        )
        assert refused.status_code == 404, refused.text

    async with app_session(tenant) as db:
        assigned = (
            await db.execute(
                sa.select(sa.func.count())
                .select_from(m.OrgMember)
                .where(m.OrgMember.tenant_id == tenant, m.OrgMember.role_id.is_not(None))
            )
        ).scalar_one()
    assert assigned == 0, (
        f"{assigned} of 3 people were assigned by a request that answered 404; "
        "the caller cannot tell which"
    )


async def test_a_csv_can_drive_the_assignment_by_subject(
    app_session: AppSessionFactory,
) -> None:
    """`?subject=` in place of the member id.

    The subject is the one identifier an HR export and a JWT agree on, so a
    spreadsheet can drive this route without resolving forty uuids first. It
    RESOLVES ONLY -- a subject nobody enrolled is a 404 that points at
    `POST /members` -- because minting a person as a side effect of assigning
    them a role turns a typo in a spreadsheet into a permanent row nobody
    enrolled and nobody can sign in as.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        role_id = await _role(http, tenant, "Freigabe", FREIGABE)
        await _member(http, tenant, "anna")

        assigned = await http.put(
            "/api/v1/members/-/role?subject=anna",
            json={"roleId": role_id},
            headers=_headers(tenant),
        )
        assert assigned.status_code < 300, assigned.text

        unknown = await http.put(
            "/api/v1/members/-/role?subject=niemand",
            json={"roleId": role_id},
            headers=_headers(tenant),
        )
        assert unknown.status_code == 404, unknown.text
        assert "POST /members" in unknown.text, (
            "the refusal does not say how to enrol them, which is the next thing "
            "the caller has to do"
        )

    async with app_session(tenant) as db:
        rows = (
            (await db.execute(sa.select(m.OrgMember).where(m.OrgMember.tenant_id == tenant)))
            .scalars()
            .all()
        )
    assert {r.subject for r in rows} == {"anna"}, "a 404 by subject minted somebody"
    assert str(rows[0].role_id) == role_id


# ---------------------------------------------------------------- the trail


async def test_every_role_write_leaves_a_trail_naming_what_changed(
    app_session: AppSessionFactory,
) -> None:
    """Create, assign, edit, delete -- four events, and the edit carries a DIFF.

    The diff is the assertion that matters. `role.updated` recording only the
    resulting set says that something changed and refuses to say what, and the
    question this trail is read for a year later is "when did team leads stop
    being able to decide approvals", which only a `removed` answers.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        role_id = await _role(http, tenant, "Abteilungsleitung", FREIGABE)
        member_id = await _member(http, tenant, "anna")
        assert (
            await http.put(
                f"/api/v1/members/{member_id}/role",
                json={"roleId": role_id},
                headers=_headers(tenant),
            )
        ).status_code < 300
        assert (
            await http.put(
                f"/api/v1/roles/{role_id}",
                json={"description": "", "permissions": [APPROVAL_VIEW, AUDIT_VIEW]},
                headers=_headers(tenant),
            )
        ).status_code < 300
        assert (
            await http.put(
                f"/api/v1/members/{member_id}/role",
                json={"roleId": None},
                headers=_headers(tenant),
            )
        ).status_code < 300
        assert (
            await http.delete(f"/api/v1/roles/{role_id}", headers=_headers(tenant))
        ).status_code < 300

    by_action = {e.action: e for e in await _events(app_session, tenant)}
    assert {
        "role.created",
        "member.role_assigned",
        "role.updated",
        "member.role_cleared",
        "role.deleted",
    } <= set(by_action), sorted(by_action)

    updated = by_action["role.updated"]
    assert set(updated.resource["added"]) == {AUDIT_VIEW}
    assert set(updated.resource["removed"]) == {APPROVAL_DECIDE, CLARIFICATION_VIEW}
    assert updated.resource["holder_count"] == 1, (
        "the trail does not record how many people the edit changed"
    )
    # Attributed to the person, not to the system: `boss` has a member row by now
    # because `POST /members` minted one for the caller on the way through.
    assert by_action["role.created"].responsible_id == "boss"


# ------------------------------------------------------------ the two lists


async def test_the_agents_own_role_is_not_offered_as_a_human_role(
    app_session: AppSessionFactory,
) -> None:
    """`GET /roles` is a picker of roles for PEOPLE.

    `agent_default` is a row in the same table whose entire content is
    `tool:read`, `tool:write`, `tool:send`, and `create_tenant` writes one for
    every tenant. Offering it here would put an agent's vocabulary in a list an
    administrator assigns from -- and the assignment itself is refused, so the
    only thing listing it can produce is somebody trying.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_role = m.Role(tenant_id=tenant, name=AGENT_DEFAULT, builtin=True, kind="agent")
        db.add(agent_role)
        await db.flush()
        agent_role_id = str(agent_role.id)
        member = m.OrgMember(
            tenant_id=tenant,
            subject="anna",
            subject_uuid=uuid.uuid4(),
            display_name="Anna",
        )
        db.add(member)
        await db.flush()
        member_id = str(member.id)

    async with _http() as http:
        listed = await http.get("/api/v1/roles", headers=_headers(tenant))
        assert listed.status_code == 200, listed.text
        assert AGENT_DEFAULT not in {r["name"] for r in listed.json()}

        refused = await http.put(
            f"/api/v1/members/{member_id}/role",
            json={"roleId": agent_role_id},
            headers=_headers(tenant),
        )
        assert refused.status_code == 422, (
            "a person was pointed at an agent-kind role; they now resolve to the "
            "empty set, which looks on every screen like a role that grants nothing"
        )

    async with app_session(tenant) as db:
        anna = await db.get(m.OrgMember, uuid.UUID(member_id))
    assert anna is not None and anna.role_id is None


async def test_a_deleted_roles_name_is_not_handed_to_a_new_one(
    app_session: AppSessionFactory,
) -> None:
    """The tombstone, at the door.

    Renaming is refused, so delete-and-recreate is the sanctioned way to change a
    name -- which is exactly why `uq_role_tenant_name` is unconditional. A freed
    name would hand every mention of the old role in the audit trail to a new one
    that never earned them, and nothing on any screen would say so.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        role_id = await _role(http, tenant, "Freigabe Vertrieb", FREIGABE)
        assert (
            await http.delete(f"/api/v1/roles/{role_id}", headers=_headers(tenant))
        ).status_code < 300

        again = await http.post(
            "/api/v1/roles",
            json={"name": "freigabe vertrieb", "description": "", "permissions": []},
            headers=_headers(tenant),
        )
        assert again.status_code == 409, again.text

        # And the deleted role is gone from the list and from the detail door.
        listed = await http.get("/api/v1/roles", headers=_headers(tenant))
        assert "Freigabe Vertrieb" not in {r["name"] for r in listed.json()}
        assert (
            await http.get(f"/api/v1/roles/{role_id}", headers=_headers(tenant))
        ).status_code == 404
