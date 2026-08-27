"""The feature, end to end: an IT admin composes a role and a person gets it.

Everything else in this slice is a guard on this file. If `test_an_assigned_role_
reaches_the_routes_its_grants_name_and_no_others` does not pass, there is no
feature -- there is a schema, a resolver and a form.

Two properties are asserted here that cannot be asserted anywhere else:

* **The grants reach the ROUTES.** A role is a set of strings until a route is
  admitted or refused because of it. The previous slice's lesson is that a green
  unit test on a resolver proves nothing about a door.
* **A revocation lands on the NEXT REQUEST.** Same token, no refresh, no TTL.
  That is what buys the right to keep authority over money out of the JWT and out
  of a cache, and it is the reason §5.4's snapshot cache is deliberately not
  built.

`test_a_permission_row_cannot_carry_an_object_or_a_constraint` -- test 28 of the
design's §9 -- is asserted against the migrated schema in
`tests/db/test_migration_0047.py`, where the other CHECK-constraint claims live.
It is a statement about Postgres, not about this router, and a second copy here
would be a copy that can disagree.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.authz.permissions import (
    ALL_PERMISSIONS,
    APPROVAL,
    APPROVAL_DECIDE,
    AUDITOR,
    BUILTIN_ROLE_PERMISSIONS,
    CLARIFICATION_ANSWER,
    CLARIFICATION_VIEW,
    DELEGATABLE_PERMISSIONS,
    DEPT_MANAGER,
    MANAGE,
    MEMBER,
    NEVER_DELEGATABLE,
    NOT_YET_DELEGATABLE,
    OPERATOR,
    ORG_ADMIN,
    ROLE,
    VIEW,
    delegation_refusal,
    perm,
)
from oc8.authz.scope import subject_uuid_for
from oc8.main import create_app
from tests.conftest import AppSessionFactory

# No module-level `pytest.mark.asyncio`: this file mixes async doors with
# synchronous source sweeps, and `asyncio_mode = "auto"` already collects the
# async ones.

APPROVAL_VIEW = perm(APPROVAL, VIEW)

#: What an IT admin actually composes on day one: somebody who signs off offers
#: and answers the questions agents park, and reads nothing else.
FREIGABE = [APPROVAL_VIEW, APPROVAL_DECIDE, CLARIFICATION_VIEW, CLARIFICATION_ANSWER]


def _headers(tenant: uuid.UUID, subject: str, role: str = ORG_ADMIN) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=role)
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


class _Office:
    tenant: uuid.UUID
    department: uuid.UUID
    approval: uuid.UUID
    anna: uuid.UUID


async def _office(
    app_session: AppSessionFactory, *, subject: str = "anna", all_departments: bool = True
) -> _Office:
    """One department, one pending approval in it, and one employee with a member
    row -- so every refusal below is about the ROLE and never about the seat.

    `all_departments` is what stands in for a seat, and it has to be chosen per
    test rather than defaulted for all of them, because it is not neutral in
    either direction (§0.A: a person's authority is their role PLUS their seats,
    and this flag is the company-wide seat):

    * **True** -- needed wherever the test DECIDES an approval. The role admits
      her at the door on its tenant-wide `approval:decide`, and then
      `decide_approval` asks the scope again as defence in depth; a person with a
      role and no seat anywhere is refused there, so the flagship test would 404
      on a scope question rather than proving anything about roles.
    * **False** -- needed wherever the test asserts that removing a permission
      from the role REFUSES her. A seat carries `approval:view` and
      `approval:decide` in its own right, and this flag carries them everywhere,
      so a seated person is admitted at `require_departmental` whatever her role
      says. With it on, "revocation lands on the next request" is unobservable at
      that door -- not because the revocation did not land, but because the
      second source of the same permission never went away.
    """
    office = _Office()
    office.tenant = uuid.uuid4()
    async with app_session(office.tenant) as db:
        dept = m.Department(tenant_id=office.tenant, name="Vertrieb")
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=office.tenant, department_id=dept.id, name="Nora")
        db.add(agent)
        await db.flush()
        approval = m.ApprovalRequest(
            tenant_id=office.tenant,
            agent_id=agent.id,
            department_id=dept.id,
            action_type="tool_send",
            status="pending",
            title="Angebot Gartenholz GmbH",
            detail="",
            amount_text="4.320,00 EUR",
            payload={"tool": "odoo.send_quotation", "arguments": {}},
        )
        db.add(approval)
        member = m.OrgMember(
            tenant_id=office.tenant,
            subject=subject,
            subject_uuid=subject_uuid_for(subject),
            display_name="Anna",
            all_departments=all_departments,
        )
        db.add(member)
        await db.flush()
        office.department, office.approval, office.anna = dept.id, approval.id, member.id
    return office


async def _create_role(
    http: AsyncClient, tenant: uuid.UUID, name: str, permissions: list[str]
) -> dict[str, Any]:
    created = await http.post(
        "/api/v1/roles",
        json={"name": name, "description": "Von der IT angelegt", "permissions": permissions},
        headers=_headers(tenant, "boss"),
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


# ----------------------------------------------------------------- the feature


async def test_an_assigned_role_reaches_the_routes_its_grants_name_and_no_others(
    app_session: AppSessionFactory,
) -> None:
    """The test that proves the feature exists.

    Anna's token says `org_admin`, because that is what every token on every live
    tenant says. Her administrator composes "Freigabe Vertrieb" out of four boxes
    and assigns it. On her very next request she is admitted to the two doors
    those four boxes name and refused at every other one -- including the ones
    her TOKEN still claims.
    """
    office = await _office(app_session)
    hers = _headers(office.tenant, "anna", ORG_ADMIN)

    async with _http() as http:
        role = await _create_role(http, office.tenant, "Freigabe Vertrieb", FREIGABE)

        assigned = await http.put(
            f"/api/v1/members/{office.anna}/role",
            json={"roleId": role["id"]},
            headers=_headers(office.tenant, "boss"),
        )
        assert assigned.status_code < 300, assigned.text

        for path in ("/api/v1/approvals", "/api/v1/clarifications"):
            granted = await http.get(path, headers=hers)
            assert granted.status_code == 200, f"{path}: {granted.status_code} {granted.text}"

        decided = await http.post(
            f"/api/v1/approvals/{office.approval}/decision",
            json={"decision": "approve"},
            headers=hers,
        )
        assert decided.status_code == 200, decided.text

        # And everything her token would still have carried. `/api/v1/agents` is
        # deliberately NOT in this list: `agent:view` graduated into the seat
        # view level (department-scoped-agent-authority design, decision 2), so
        # `office`'s `all_departments=True` -- the company-wide seat this test
        # needs for the DECIDE assertion above -- admits her there on its own,
        # independent of "Freigabe Vertrieb". That is the seat term working as
        # designed, not the role reaching further than it was composed to.
        for path in ("/api/v1/capas", "/api/v1/budgets", "/api/v1/secrets"):
            refused = await http.get(path, headers=hers)
            assert refused.status_code == 403, (
                f"{path} admitted a demoted administrator: {refused.status_code}"
            )


async def test_revoking_an_assignment_takes_effect_on_the_next_request(
    app_session: AppSessionFactory,
) -> None:
    """Same token. No refresh, no TTL, no cache to invalidate.

    Both directions, because only one of them is the dangerous one: editing a
    role to ADD a permission is a convenience, editing it to REMOVE one is a
    revocation, and a revocation that lands in five minutes is a revocation that
    did not land.

    Anna holds NO seat and no company-wide flag here, which is what makes the
    refusal observable: her authority at these doors comes from the role and from
    nothing else. With a seat she would keep `approval:view` from the seat's own
    vocabulary after the role stopped granting it -- correctly, per §0.A -- and
    this test would be measuring the seat rather than the revocation.
    """
    office = await _office(app_session, all_departments=False)
    hers = _headers(office.tenant, "anna", ORG_ADMIN)
    admin = _headers(office.tenant, "boss")

    async with _http() as http:
        role = await _create_role(http, office.tenant, "Freigabe", FREIGABE)
        await http.put(
            f"/api/v1/members/{office.anna}/role", json={"roleId": role["id"]}, headers=admin
        )
        assert (await http.get("/api/v1/approvals", headers=hers)).status_code == 200

        # One PUT, and the very next click.
        edited = await http.put(
            f"/api/v1/roles/{role['id']}",
            json={"description": "", "permissions": [CLARIFICATION_VIEW]},
            headers=admin,
        )
        assert edited.status_code < 300, edited.text
        assert (await http.get("/api/v1/approvals", headers=hers)).status_code == 403
        assert (await http.get("/api/v1/clarifications", headers=hers)).status_code == 200

        # And clearing the assignment restores the token floor -- NULL is "the
        # token decides", not "no permissions".
        cleared = await http.put(
            f"/api/v1/members/{office.anna}/role", json={"roleId": None}, headers=admin
        )
        assert cleared.status_code < 300, cleared.text
        assert (await http.get("/api/v1/capas", headers=hers)).status_code == 200


# ---------------------------------------------------------------- the refusals


async def test_a_non_delegatable_permission_is_refused_and_named() -> None:
    """422 naming the offender and the reason, never a silent drop.

    An admin who ticks `plugin:manage`, sees "saved", and walks away believing
    the team lead can install plugins has been told something false by the
    product. The reason string is rendered next to the box, so it is part of the
    contract and not decoration.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        for permission in ("plugin:manage", "approval:decide_any", "tool:send", "run:view"):
            refused = await http.post(
                "/api/v1/roles",
                json={
                    "name": f"Zu viel {permission}",
                    "description": "",
                    "permissions": [APPROVAL_VIEW, permission],
                },
                headers=_headers(tenant, "boss"),
            )
            assert refused.status_code == 422, f"{permission}: {refused.status_code}"
            assert permission in refused.text
            reason = delegation_refusal(permission)
            assert reason is not None
            # A few words of it, so a reworded reason is not a test failure while
            # a MISSING reason is.
            assert reason.split()[0].strip(".,") in refused.text, refused.text


async def test_a_role_may_not_be_named_after_a_builtin() -> None:
    """409, and independently of `uq_role_tenant_name`.

    The index cannot carry this: Globex has ONE `role` row, so on that tenant
    `operator` is a free name -- and the row it created would then be ignored by
    the resolver for ever, because `builtin` is false and the code table is never
    consulted for it. An admin would have composed a role called `operator` that
    grants exactly what he ticked while every screen says `operator` means
    something else.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        for name in sorted(BUILTIN_ROLE_PERMISSIONS):
            refused = await http.post(
                "/api/v1/roles",
                json={"name": name, "description": "", "permissions": []},
                headers=_headers(tenant, "boss"),
            )
            assert refused.status_code == 409, f"{name}: {refused.status_code} {refused.text}"

        # And the same name in a different case, since the uniqueness is on
        # `lower(name)` and the refusal must agree with it.
        assert (
            await http.post(
                "/api/v1/roles",
                json={"name": "Operator", "description": "", "permissions": []},
                headers=_headers(tenant, "boss"),
            )
        ).status_code == 409


async def test_a_role_that_somebody_holds_cannot_be_deleted_silently(
    app_session: AppSessionFactory,
) -> None:
    """409 naming the holders, because the alternative is a silent re-promotion.

    `ON DELETE RESTRICT` is what makes this a refusal rather than a demotion or a
    deletion of the person, and the endpoint's job is to turn the IntegrityError
    into something an administrator can act on: who holds it, and the offer to
    move them.
    """
    office = await _office(app_session)
    admin = _headers(office.tenant, "boss")

    async with _http() as http:
        role = await _create_role(http, office.tenant, "Freigabe", FREIGABE)
        await http.put(
            f"/api/v1/members/{office.anna}/role", json={"roleId": role["id"]}, headers=admin
        )

        refused = await http.delete(f"/api/v1/roles/{role['id']}", headers=admin)
        assert refused.status_code == 409, refused.text
        assert "Anna" in refused.text or str(office.anna) in refused.text, (
            "the 409 does not say who holds it, so the administrator cannot act on it"
        )

    async with app_session(office.tenant) as db:
        member = await db.get(m.OrgMember, office.anna)
    assert member is not None and member.role_id is not None, (
        "a refused delete demoted the holder anyway"
    )


async def test_reassigning_holders_on_delete_is_bounded_by_the_callers_own_set(
    app_session: AppSessionFactory,
) -> None:
    """`?reassignTo=` moves every holder in one transaction -- and it is a grant.

    Without the subset rule on the TARGET, `DELETE /roles/{weak}?reassignTo=
    {strong}` is a one-call bypass of the whole guard: the caller never names a
    permission, so nothing looks like an escalation, and forty people change
    authority. The rule is `(before | after) ⊆ caller.tenant_wide` applied to the
    target as well as to the body.

    Today the only caller who reaches this route is `org_admin` -- `role:manage`
    and `member:manage` are both NEVER_DELEGATABLE -- so the bound itself is
    vacuous and `test_the_subset_rule_is_vacuous_today_and_will_not_stay_that_way`
    is what fails when that changes. What is NOT vacuous today, and is asserted
    here, is that the reassign path exists, validates its target, and actually
    moves the holders in one transaction rather than deleting the role and
    leaving them pointing at nothing.
    """
    office = await _office(app_session)
    admin = _headers(office.tenant, "boss")

    other_tenant = uuid.uuid4()
    async with _http() as http:
        weak = await _create_role(http, office.tenant, "Freigabe alt", FREIGABE)
        strong = await _create_role(http, office.tenant, "Freigabe neu", FREIGABE)
        foreign = await _create_role(http, other_tenant, "Fremd", FREIGABE)

        await http.put(
            f"/api/v1/members/{office.anna}/role", json={"roleId": weak["id"]}, headers=admin
        )

        for target, why in (
            (foreign["id"], "another tenant's role"),
            (str(uuid.uuid4()), "a role that never existed"),
            (weak["id"], "the role being deleted"),
        ):
            refused = await http.delete(
                f"/api/v1/roles/{weak['id']}?reassignTo={target}", headers=admin
            )
            assert refused.status_code in (404, 409, 422), f"{why}: {refused.status_code}"

        moved = await http.delete(
            f"/api/v1/roles/{weak['id']}?reassignTo={strong['id']}", headers=admin
        )
        assert moved.status_code < 300, moved.text

    async with app_session(office.tenant) as db:
        member = await db.get(m.OrgMember, office.anna)
    assert member is not None
    assert str(member.role_id) == strong["id"], (
        "the holder was not moved; the role is gone and she is an org_admin again"
    )


async def test_you_may_not_demote_yourself(app_session: AppSessionFactory) -> None:
    """409, and -- the half that matters -- he still holds what he held.

    The lockout this prevents is total: `member:manage` is the permission that
    assigns roles, so an administrator who assigns himself a role without it has
    locked the only door back. `oc8 member set-role --clear`, running as the
    schema owner, is the out-of-band repair, and it should be the only lockout
    repair anybody ever needs.
    """
    office = await _office(app_session, subject="boss")
    admin = _headers(office.tenant, "boss")

    async with _http() as http:
        role = await _create_role(http, office.tenant, "Freigabe", FREIGABE)
        refused = await http.put(
            f"/api/v1/members/{office.anna}/role", json={"roleId": role["id"]}, headers=admin
        )
        assert refused.status_code == 409, refused.text

        # Unchanged, and still able to administer.
        assert (await http.get("/api/v1/members", headers=admin)).status_code == 200
        assert (await http.get("/api/v1/capas", headers=admin)).status_code == 200

    async with app_session(office.tenant) as db:
        member = await db.get(m.OrgMember, office.anna)
    assert member is not None and member.role_id is None


async def test_only_member_manage_may_assign_and_only_role_manage_may_author(
    app_session: AppSessionFactory,
) -> None:
    """The two authorities are separate, and neither is reachable from any
    built-in role but `org_admin`.

    `operator` runs the office, `dept_manager` runs a department and holds nine
    `:manage` permissions, `auditor` reads everything -- and none of them may
    mint authority or hand it out. A role that mints roles is a role that mints
    root.
    """
    office = await _office(app_session)

    async with _http() as http:
        role = await _create_role(http, office.tenant, "Freigabe", FREIGABE)

        for name in (OPERATOR, DEPT_MANAGER, AUDITOR):
            theirs = _headers(office.tenant, f"jemand-{name}", name)
            authored = await http.post(
                "/api/v1/roles",
                json={"name": f"Meins {name}", "description": "", "permissions": []},
                headers=theirs,
            )
            assert authored.status_code == 403, f"{name} authored a role: {authored.text}"

            edited = await http.put(
                f"/api/v1/roles/{role['id']}",
                json={"description": "", "permissions": []},
                headers=theirs,
            )
            assert edited.status_code == 403, f"{name} edited a role: {edited.text}"

            deleted = await http.delete(f"/api/v1/roles/{role['id']}", headers=theirs)
            assert deleted.status_code == 403, f"{name} deleted a role: {deleted.text}"

            assigned = await http.put(
                f"/api/v1/members/{office.anna}/role",
                json={"roleId": role["id"]},
                headers=theirs,
            )
            assert assigned.status_code == 403, f"{name} assigned a role: {assigned.text}"

            # Reading the tenant's roles is a different, deliberately wider
            # right: `role:view` sweeps into `_VIEW_EVERYTHING`.
            listed = await http.get("/api/v1/roles", headers=theirs)
            assert listed.status_code == 200, f"{name} may not even read the roles: {listed.text}"


def test_the_subset_rule_is_vacuous_today_and_will_not_stay_that_way() -> None:
    """A forcing function rather than a test of behaviour, and it says so.

    `(before | after) ⊆ caller.tenant_wide` cannot bite while the only caller who
    holds `role:manage` or `member:manage` is `org_admin`, who holds all 52 -- so
    every subset test above is arithmetic on a superset. The day either
    permission graduates out of `NEVER_DELEGATABLE`, an administrator can compose
    a role that composes roles, and this fails: whoever graduates it has to make
    those tests real first, with a caller who genuinely holds less than he is
    handing out.
    """
    minting = {perm(ROLE, MANAGE), perm(MEMBER, MANAGE)}
    assert minting <= set(NEVER_DELEGATABLE), sorted(minting - set(NEVER_DELEGATABLE))
    holders = {name for name, granted in BUILTIN_ROLE_PERMISSIONS.items() if minting & set(granted)}
    assert holders == {ORG_ADMIN}, (
        f"{sorted(holders)} can now mint or hand out authority. The subset rule "
        "in roles/service.py is no longer vacuous -- write the test where a "
        "caller is refused a permission he does not himself hold."
    )


# ------------------------------------------------------------- the catalogue


async def test_the_catalogue_offers_the_delegatable_and_explains_the_rest() -> None:
    """The screen renders `p.split(":")[1]` today, so `supervision:manage`,
    `handoff:manage`, `contract:manage` and `flow:manage` are four different
    words that all read as "manage" to a layperson.

    The refused entries are RETURNED, not filtered out: a hidden control produces
    a support ticket, and explaining its own refusal is this screen's whole
    ethos.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        got = await http.get(
            "/api/v1/permissions/catalogue", headers=_headers(tenant, "irgendwer", OPERATOR)
        )
    assert got.status_code == 200, got.text
    entries = {e["permission"]: e for e in got.json()}

    assert set(entries) == set(ALL_PERMISSIONS), sorted(set(ALL_PERMISSIONS) ^ set(entries))
    for permission, entry in entries.items():
        assert entry["delegatable"] is (permission in DELEGATABLE_PERMISSIONS), permission
        assert entry["label"].strip(), permission
        if not entry["delegatable"]:
            assert entry["reason"], f"{permission} is refused and says nothing"
    refused = set(NOT_YET_DELEGATABLE) | set(NEVER_DELEGATABLE)
    assert {p for p, e in entries.items() if not e["delegatable"]} == refused


async def test_the_role_list_names_its_holders_and_what_may_be_edited(
    app_session: AppSessionFactory,
) -> None:
    """`holderCount` is the blast radius, shown before Save.

    "Jede Teamleitung darf ab jetzt auch das Prüfprotokoll lesen" is one PUT that
    changes forty people's authority. An administrator who cannot see the forty
    before he clicks is being asked to guess.
    """
    office = await _office(app_session)
    admin = _headers(office.tenant, "boss")

    async with _http() as http:
        role = await _create_role(http, office.tenant, "Freigabe", FREIGABE)
        await http.put(
            f"/api/v1/members/{office.anna}/role", json={"roleId": role["id"]}, headers=admin
        )
        listed = await http.get("/api/v1/roles", headers=admin)
        assert listed.status_code == 200, listed.text
        rows = {r["name"]: r for r in listed.json()}

        assert rows["Freigabe"]["holderCount"] == 1
        assert rows["Freigabe"]["canEdit"] is True
        # The built-ins are listed and are read-only: their grants come from the
        # code table, so a `role_permission` row against one is inert and an
        # editor over it would be a lie.
        for name in (ORG_ADMIN, OPERATOR):
            assert rows[name]["canEdit"] is False, name

        detail = await http.get(f"/api/v1/roles/{role['id']}", headers=admin)
        assert detail.status_code == 200, detail.text
        assert sorted(detail.json()["permissions"]) == sorted(FREIGABE)
        assert [h["memberId"] for h in detail.json()["holders"]] == [str(office.anna)]


async def test_a_role_stops_at_the_tenant_boundary(app_session: AppSessionFactory) -> None:
    """A role's name is a tenant's own word and its grants are its own
    configuration. Asserted at the door as well as at the table, because RLS is
    what enforces it and "it is obviously scoped" is what was said about
    `GET /approvals` too."""
    office = await _office(app_session)
    other = uuid.uuid4()

    async with _http() as http:
        theirs = await _create_role(http, other, "Ihre Rolle", FREIGABE)

        listed = await http.get("/api/v1/roles", headers=_headers(office.tenant, "boss"))
        assert listed.status_code == 200
        assert "Ihre Rolle" not in {r["name"] for r in listed.json()}

        peeked = await http.get(
            f"/api/v1/roles/{theirs['id']}", headers=_headers(office.tenant, "boss")
        )
        assert peeked.status_code == 404, peeked.text

        borrowed = await http.put(
            f"/api/v1/members/{office.anna}/role",
            json={"roleId": theirs["id"]},
            headers=_headers(office.tenant, "boss"),
        )
        assert borrowed.status_code in (404, 422), borrowed.text


# ------------------------------------------------ standalone 2FA grace clock


async def _org_admin_role(app_session: AppSessionFactory, tenant: uuid.UUID) -> uuid.UUID:
    """`_create_role` refuses a builtin name (see
    test_a_role_may_not_be_named_after_a_builtin above) -- insert the row
    directly, the same way test_password_auth.py's org_in_db fixture does."""
    async with app_session(tenant) as db:
        role = m.Role(tenant_id=tenant, name=ORG_ADMIN, builtin=True)
        db.add(role)
        await db.flush()
        return role.id


async def test_assigning_org_admin_starts_the_grace_clock_when_no_credential_exists(
    app_session: AppSessionFactory,
) -> None:
    office = await _office(app_session)
    role_id = await _org_admin_role(app_session, office.tenant)

    async with _http() as http:
        assigned = await http.put(
            f"/api/v1/members/{office.anna}/role",
            json={"roleId": str(role_id)},
            headers=_headers(office.tenant, "boss"),
        )
        assert assigned.status_code < 300, assigned.text

    async with app_session(office.tenant) as db:
        member = await db.get(m.OrgMember, office.anna)
        assert member is not None
        assert member.totp_grace_started_at is not None


async def test_assigning_org_admin_does_not_restart_an_already_running_clock(
    app_session: AppSessionFactory,
) -> None:
    office = await _office(app_session)
    role_id = await _org_admin_role(app_session, office.tenant)

    async with _http() as http:
        first = await http.put(
            f"/api/v1/members/{office.anna}/role",
            json={"roleId": str(role_id)},
            headers=_headers(office.tenant, "boss"),
        )
        assert first.status_code < 300, first.text

    async with app_session(office.tenant) as db:
        member = await db.get(m.OrgMember, office.anna)
        assert member is not None
        first_started_at = member.totp_grace_started_at
        assert first_started_at is not None

    # Re-confirming the SAME assignment (e.g. an operator re-saving a form
    # unchanged) must not reset the countdown back to full.
    async with _http() as http:
        second = await http.put(
            f"/api/v1/members/{office.anna}/role",
            json={"roleId": str(role_id)},
            headers=_headers(office.tenant, "boss"),
        )
        assert second.status_code < 300, second.text

    async with app_session(office.tenant) as db:
        member = await db.get(m.OrgMember, office.anna)
        assert member is not None
        assert member.totp_grace_started_at == first_started_at


async def test_demoting_away_from_org_admin_clears_the_clock_if_never_enrolled(
    app_session: AppSessionFactory,
) -> None:
    office = await _office(app_session)
    role_id = await _org_admin_role(app_session, office.tenant)

    async with _http() as http:
        grant = await http.put(
            f"/api/v1/members/{office.anna}/role",
            json={"roleId": str(role_id)},
            headers=_headers(office.tenant, "boss"),
        )
        assert grant.status_code < 300, grant.text

    async with app_session(office.tenant) as db:
        member = await db.get(m.OrgMember, office.anna)
        assert member is not None
        assert member.totp_grace_started_at is not None

    async with _http() as http:
        cleared = await http.put(
            f"/api/v1/members/{office.anna}/role",
            json={"roleId": None},
            headers=_headers(office.tenant, "boss"),
        )
        assert cleared.status_code < 300, cleared.text

    async with app_session(office.tenant) as db:
        member = await db.get(m.OrgMember, office.anna)
        assert member is not None
        assert member.totp_grace_started_at is None


async def test_demoting_an_enrolled_admin_does_not_touch_the_clock_because_its_already_none(
    app_session: AppSessionFactory,
) -> None:
    office = await _office(app_session)
    role_id = await _org_admin_role(app_session, office.tenant)

    async with _http() as http:
        grant = await http.put(
            f"/api/v1/members/{office.anna}/role",
            json={"roleId": str(role_id)},
            headers=_headers(office.tenant, "boss"),
        )
        assert grant.status_code < 300, grant.text

    # Simulate having enrolled: a real TotpCredential row exists, and the
    # clock is cleared the way /auth/totp/confirm's own logic will clear it
    # (Task 5 doesn't touch totp_grace_started_at directly -- this fixture
    # stands in for that until Task 5/8 are both live end to end).
    async with app_session(office.tenant) as db:
        member = await db.get(m.OrgMember, office.anna)
        assert member is not None
        member.totp_grace_started_at = None
        db.add(
            m.TotpCredential(
                tenant_id=office.tenant,
                member_id=office.anna,
                secret_ref=f"totp:{office.anna}",
                enrolled_at=dt.datetime.now(tz=dt.UTC),
                backup_codes=[],
            )
        )
        await db.flush()

    async with _http() as http:
        cleared = await http.put(
            f"/api/v1/members/{office.anna}/role",
            json={"roleId": None},
            headers=_headers(office.tenant, "boss"),
        )
        assert cleared.status_code < 300, cleared.text

    async with app_session(office.tenant) as db:
        member = await db.get(m.OrgMember, office.anna)
        assert member is not None
        assert member.totp_grace_started_at is None


async def test_assigning_org_admin_does_not_start_the_clock_for_an_already_enrolled_member(
    app_session: AppSessionFactory,
) -> None:
    """Coverage gap the Task 8 review flagged: all four other grace-clock
    tests use a member with no TotpCredential, so a query that always
    evaluated has_credential=False would still pass them. This pins the
    `has_credential` check on its own -- a member who already holds a
    credential must NOT get a clock started when granted org_admin."""
    office = await _office(app_session)
    role_id = await _org_admin_role(app_session, office.tenant)

    async with app_session(office.tenant) as db:
        db.add(
            m.TotpCredential(
                tenant_id=office.tenant,
                member_id=office.anna,
                secret_ref=f"totp:{office.anna}",
                enrolled_at=dt.datetime.now(tz=dt.UTC),
                backup_codes=[],
            )
        )
        await db.flush()

    async with _http() as http:
        assigned = await http.put(
            f"/api/v1/members/{office.anna}/role",
            json={"roleId": str(role_id)},
            headers=_headers(office.tenant, "boss"),
        )
        assert assigned.status_code < 300, assigned.text

    async with app_session(office.tenant) as db:
        member = await db.get(m.OrgMember, office.anna)
        assert member is not None
        assert member.totp_grace_started_at is None
