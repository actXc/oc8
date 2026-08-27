"""Four refusals the write path was missing, each one reachable by accident.

None of these is an attack. Every one of them is what happens when a client is
sloppy, a proxy drops a key, an administrator deletes the role he is standing
in, or somebody opens a URL before logging in -- and in all four cases the answer
was 200 or 401-shaped-as-200 rather than a sentence.

* **An omitted `permissions` key revoked everything.** `UpdateRoleRequest`
  defaulted it, so "I did not mention permissions" and "take all twenty-one
  grants away from forty people" were the same wire message, answered 200 with
  an audit event recording the revocation as intended.
* **`?reassignTo=` moved the caller without the self-demotion check.**
  `PUT /members/{id}/role` refuses an administrator who would take his own
  `member:manage` away; `DELETE /roles/{his}?reassignTo={weak}` did exactly that
  and was not checked, because it is an assignment wearing a different verb.
* **`GET /permissions/catalogue` answered anonymous callers.** `unguarded()` has
  always meant "no operator PERMISSION"; the handler took no `CurrentPrincipal`,
  so `HTTPBearer` never ran and the product's entire authority model -- with the
  refusal prose attached -- was readable before login.
* **The 503 branch had no test at all.** A database blip must not answer 403:
  that locks every operator out with the status code a genuine refusal uses, and
  the screen then tells them to ask an administrator for a permission they
  already hold.
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
from sqlalchemy.exc import OperationalError

from oc8 import models as m
from oc8.api import deps
from oc8.auth import get_identity_provider
from oc8.authz.permissions import (
    APPROVAL,
    APPROVAL_DECIDE,
    CLARIFICATION_VIEW,
    MANAGE,
    MEMBER,
    ORG_ADMIN,
    VIEW,
    perm,
)
from oc8.authz.scope import subject_uuid_for
from oc8.main import create_app
from oc8.roles.service import RoleRefused, delete_role, load_role
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

APPROVAL_VIEW = perm(APPROVAL, VIEW)
MEMBER_MANAGE = perm(MEMBER, MANAGE)
MEMBER_VIEW = perm(MEMBER, VIEW)
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


async def _role(
    http: AsyncClient, tenant: uuid.UUID, name: str, permissions: list[str]
) -> dict[str, Any]:
    created = await http.post(
        "/api/v1/roles",
        json={"name": name, "description": "Von der IT angelegt", "permissions": permissions},
        headers=_headers(tenant),
    )
    assert created.status_code == 201, created.text
    result: dict[str, Any] = created.json()
    return result


async def _member(
    app_session: AppSessionFactory,
    tenant: uuid.UUID,
    subject: str,
    role_id: str | None = None,
) -> uuid.UUID:
    async with app_session(tenant) as db:
        member = m.OrgMember(
            tenant_id=tenant,
            subject=subject,
            subject_uuid=subject_uuid_for(subject),
            display_name=subject.title(),
            role_id=None if role_id is None else uuid.UUID(role_id),
        )
        db.add(member)
        await db.flush()
        return member.id


# ------------------------------------------------ 1. omitting is not revoking


async def test_an_edit_that_names_no_permissions_is_refused_not_obeyed(
    app_session: AppSessionFactory,
) -> None:
    """A body without `permissions` is a bug, not an instruction to revoke.

    Full replacement is the right shape for this route -- a PATCH would make two
    administrators each land half an edit -- but a replacement of a field the
    caller never named is not a replacement, it is a default. On the one route
    whose whole job is "this lands for forty people on their next request", the
    difference between "the client omitted a key" and "take everything away" has
    to be visible on the wire.

    The second assertion is the one that matters: the role is UNCHANGED. A 422
    that had already deleted the rows would be a worse answer than a 200.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        role = await _role(http, tenant, "Abteilungsleitung", FREIGABE)

        refused = await http.put(
            f"/api/v1/roles/{role['id']}",
            json={"description": "Leitet eine Abteilung"},
            headers=_headers(tenant),
        )
        assert refused.status_code == 422, refused.text

        # And the same for a body that names permissions and drops the sentence:
        # the description is what tells the next administrator what the role is
        # for, and silently blanking it is the same class of loss.
        assert (
            await http.put(
                f"/api/v1/roles/{role['id']}",
                json={"permissions": FREIGABE},
                headers=_headers(tenant),
            )
        ).status_code == 422

        after = await http.get(f"/api/v1/roles/{role['id']}", headers=_headers(tenant))
        assert sorted(after.json()["permissions"]) == sorted(FREIGABE), (
            "a body that named no permissions took the role's grants away anyway"
        )


# ------------------------------------ 2. ?reassignTo= is an assignment as well


async def test_you_may_not_demote_yourself_by_deleting_the_role_you_hold(
    app_session: AppSessionFactory,
) -> None:
    """The same lockout as `PUT /members/{id}/role`, through a different verb.

    `?reassignTo=` moves every holder of the role being deleted, and the caller
    can be one of them. Moving himself to a role without `member:manage` -- which
    is every tenant-defined role there can be, because `member:manage` is
    `NEVER_DELEGATABLE` -- locks the only door back exactly as surely as
    assigning it to himself by name would, and that path was refused while this
    one was not.

    **Asserted at the service, and that is not a shortcut.** The rule is vacuous
    through the route today for the same reason `bounded_by_the_caller` is, and
    more strongly: a caller pointed at a deletable tenant role resolves to that
    role's set, which cannot contain `role:manage` -- so the route answers 403
    before `delete_role` is ever entered, and an HTTP test would be asserting the
    gate rather than this rule. `delete_role` is where the rule lives and where
    it will still be on the day `role:manage` graduates and the route stops
    refusing first.

    The refusal has to leave everything alone: a 409 that had already moved the
    holders or stamped `deleted_at` would be the lockout with a status code on it.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        doomed = await _role(http, tenant, "Abteilungsleitung", FREIGABE)
        weak = await _role(http, tenant, "Praktikant", [CLARIFICATION_VIEW])
    # The administrator himself holds the role he is about to delete.
    await _member(app_session, tenant, "boss", doomed["id"])

    async with app_session(tenant) as db:
        role = await load_role(db, tenant_id=tenant, role_id=uuid.UUID(doomed["id"]))
        target = await load_role(db, tenant_id=tenant, role_id=uuid.UUID(weak["id"]))
        assert role is not None and target is not None
        with pytest.raises(RoleRefused) as refused:
            await delete_role(db, role=role, reassign_to=target, caller_subject="boss")

    assert refused.value.status_code == 409
    assert MEMBER_MANAGE in refused.value.detail, (
        "the refusal does not name the permission being taken away, so the "
        f"administrator cannot act on it: {refused.value.detail}"
    )

    async with app_session(tenant) as db:
        still_there = await load_role(db, tenant_id=tenant, role_id=uuid.UUID(doomed["id"]))
        assert still_there is not None, "the role was soft-deleted and then refused"
        holder = (
            await db.execute(
                sa.select(m.OrgMember).where(
                    m.OrgMember.tenant_id == tenant, m.OrgMember.subject == "boss"
                )
            )
        ).scalar_one()
        assert holder.role_id == uuid.UUID(doomed["id"]), "the caller was moved anyway"


async def test_reassigning_other_people_to_a_weak_role_is_still_allowed(
    app_session: AppSessionFactory,
) -> None:
    """The refusal above is about the CALLER, and only about the caller.

    An administrator cleaning up a role forty other people hold has to be able to
    move them, and a self-demotion check that refused every reassignment would
    have made `?reassignTo=` useless -- which is the failure mode of a guard
    written one predicate too wide.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        doomed = await _role(http, tenant, "Abteilungsleitung", FREIGABE)
        weak = await _role(http, tenant, "Praktikant", [CLARIFICATION_VIEW])
        anna = await _member(app_session, tenant, f"anna-{uuid.uuid4()}", doomed["id"])

        moved = await http.request(
            "DELETE",
            f"/api/v1/roles/{doomed['id']}",
            params={"reassignTo": weak["id"]},
            headers=_headers(tenant),
        )
        assert moved.status_code == 200, moved.text

    async with app_session(tenant) as db:
        member = await db.get(m.OrgMember, anna)
        assert member is not None
        assert member.role_id == uuid.UUID(weak["id"])


# ----------------------------------------- 3. unguarded is not unauthenticated


async def test_the_permission_catalogue_still_wants_a_token() -> None:
    """`unguarded()` means "no operator permission", never "no authentication".

    The catalogue carries no gate on purpose -- the caller who most needs to read
    what a right MEANS is the one who was just refused it -- but that argument is
    about a caller who is logged in. Without a `CurrentPrincipal` parameter the
    handler never resolved `HTTPBearer`, so this route answered 200 to a request
    with no `Authorization` header at all: the only such route under `/api/v1`
    besides `/auth/config`, which exists to be readable before login.

    What leaks is not a tenant's data. It is this product's whole authority model
    with the refusal prose attached, and *"integration:manage writes
    config.command/args/secret_env verbatim: arbitrary execution"* reads as a
    target list to an anonymous reader.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        anonymous = await http.get("/api/v1/permissions/catalogue")
        assert anonymous.status_code == 401, (
            f"the catalogue answered an unauthenticated caller: {anonymous.status_code}"
        )

        # And it is still ungated for anybody with a token, including the role
        # that holds nothing -- which is the caller the route exists for.
        for role in (ORG_ADMIN, "member"):
            allowed = await http.get(
                "/api/v1/permissions/catalogue", headers=_headers(tenant, "wer", role)
            )
            assert allowed.status_code == 200, f"{role} could not read the catalogue"
            assert len(allowed.json()) > 40


# --------------------------------------------- 4. a blip is a 503, not a 403


async def test_a_database_failure_at_the_gate_is_503_and_not_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The branch `deps._authority` exists for, asserted rather than described.

    A 403 during a blip locks every operator out using the status code a genuine
    refusal uses, so the screen tells them to ask an administrator for a
    permission they already hold -- and a missing column would make every gated
    route answer "somebody changed the permissions" and be chased for a day.

    Both gate factories are checked. They call the same helper, which is exactly
    why one of them silently losing the `try` would be invisible.
    """
    tenant = uuid.uuid4()

    async def _explode(*_a: Any, **_k: Any) -> Any:
        raise OperationalError("SELECT 1", {}, Exception("connection reset"))

    monkeypatch.setattr(deps, "authority_for_principal", _explode, raising=True)

    async with _http() as http:
        # `require_permission`.
        gated = await http.get("/api/v1/roles", headers=_headers(tenant))
        assert gated.status_code == 503, (
            f"a database failure was answered {gated.status_code}; a 403 here is "
            "indistinguishable from a real refusal and locks everybody out"
        )
        # `require_departmental` -- the workspace's door.
        workspace = await http.get("/api/v1/approvals?status=pending", headers=_headers(tenant))
        assert workspace.status_code in (503, 500), workspace.text
        assert workspace.status_code == 503, (
            "the departmental gate turned a database failure into something other "
            f"than a 503: {workspace.status_code} {workspace.text}"
        )


# ------------------------------------ 5. what a person holds is on the members DTO


async def test_a_member_says_which_role_they_hold(
    app_session: AppSessionFactory,
) -> None:
    """ "What does Anna hold" is answerable in one request.

    It was not. `MemberDTO` carried seats and no role, so the only view of an
    assignment was a ROLE's holder list: the question cost one panel expansion per
    role, up to thirty of them, and the assignment picker offered a person whose
    current role it could not show and then replaced it silently -- recording the
    previous role in an audit event no screen renders.

    `null` / `""` is the token floor and is NOT "no permissions". It is asserted
    explicitly, because a screen that rendered the absent case as an empty badge
    would be telling five hundred correctly-configured people that they hold
    nothing.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        role = await _role(http, tenant, "Freigabe Vertrieb", FREIGABE)
        anna = await _member(app_session, tenant, f"anna-{uuid.uuid4()}", role["id"])
        bert = await _member(app_session, tenant, f"bert-{uuid.uuid4()}")

        listed = await http.get("/api/v1/members", headers=_headers(tenant))
        assert listed.status_code == 200, listed.text
        by_id = {row["id"]: row for row in listed.json()["items"]}

        assert by_id[str(anna)]["roleId"] == role["id"]
        assert by_id[str(anna)]["roleName"] == "Freigabe Vertrieb"
        assert by_id[str(bert)]["roleId"] is None
        assert by_id[str(bert)]["roleName"] == ""


async def test_a_role_write_does_not_serialise_every_holder(
    app_session: AppSessionFactory,
) -> None:
    """`holderCount` is exact; `holders` is a preview, and the gap is on purpose.

    `RoleDetailDTO` is the response model of `POST /roles`, `PUT /roles/{id}`,
    `DELETE /roles/{id}` AND `PUT /members/{id}/role`. With an unbounded holder
    list, assigning ONE person to *Mitarbeiter* on the five-hundred-person tenant
    this design is written for serialised four hundred names into the body of an
    O(1) write and drew four hundred chips on the screen.

    The COUNT may not be capped with it. It is the blast radius -- the number an
    administrator is asked to decide on before he clicks Save -- so a preview
    length standing in for it would understate exactly the thing this feature
    exists to make visible.
    """
    from oc8.api.v1.roles import HOLDER_PREVIEW

    over = HOLDER_PREVIEW + 5
    tenant = uuid.uuid4()
    async with _http() as http:
        role = await _role(http, tenant, "Mitarbeiter", [APPROVAL_VIEW])
        for i in range(over):
            await _member(app_session, tenant, f"p{i:03d}-{uuid.uuid4()}", role["id"])

        edited = await http.put(
            f"/api/v1/roles/{role['id']}",
            json={"description": "Alle", "permissions": [APPROVAL_VIEW, CLARIFICATION_VIEW]},
            headers=_headers(tenant),
        )
        assert edited.status_code == 200, edited.text
        body = edited.json()

    assert body["holderCount"] == over, (
        "the blast radius shown before Save was capped along with the preview; "
        "the administrator is being told a smaller number than the truth"
    )
    assert len(body["holders"]) == HOLDER_PREVIEW, (
        f"{len(body['holders'])} holders travelled with an O(1) write; the list "
        "is a preview and the count is the answer"
    )
