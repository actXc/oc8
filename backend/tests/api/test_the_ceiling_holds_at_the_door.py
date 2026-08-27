"""Decision C, asserted where it is actually enforced: at the door.

`test_a_delegatable_permission_changes_no_configuration` and
`test_no_manage_permission_is_delegatable` already state the escalation ceiling,
and they state it as **set arithmetic over a constant**. That is the right test
for the catalogue and it is not a test of this system: it stays green while the
writer silently drops half the set, while the resolver hands back something the
writer never validated, and while a route the ceiling is supposed to cover is
reachable for a reason the constant knows nothing about.

Three things nothing else in this slice asserts:

1. **The whole delegatable set survives a round trip.** `validated_permissions`
   and `role_permissions` are two INDEPENDENT filters over the same 21 strings --
   one at write time naming the offender, one at resolve time making a row that
   arrived by restore inert. They agree today, and nothing compares them. If
   either drifts by one permission, an administrator ticks a box, is answered
   201, and is never told that the thing he granted does not work. Asserting one
   permission at a time (which every other test in this slice does, reasonably)
   cannot see that.

2. **The maximum is genuinely the maximum.** A role holding every offerable
   right is the strongest artefact this feature can produce, and the product's
   own diagnostic screen has to agree with it -- because `GET /governance` is
   what an administrator reads when he wants to know what he just built.

3. **The ceiling holds at a real door, for that maximum.** The previous slice
   shipped green with a seat that could reach `budget:manage` through an approval
   it was allowed to decide, so "he holds no `:manage` string" is demonstrably
   not the same claim as "he cannot change the configuration". The strongest role
   is pointed at the routes that change how the installation behaves, including
   the one that mints roles, and every one of them must refuse him.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8.auth import get_identity_provider
from oc8.authz.permissions import (
    DELEGATABLE_PERMISSIONS,
    MANAGE,
    ORG_ADMIN,
    permissions_for,
)
from oc8.main import create_app

#: Routes whose handlers change how this installation behaves, reachable by
#: method and path with no valid body required -- the gate answers first. Named
#: rather than swept, because the claim is about these SCREENS: minting a role,
#: installing code, repointing a model, enrolling somebody, moving money.
_CONFIGURATION_DOORS: list[tuple[str, str]] = [
    ("POST", "/api/v1/roles"),
    ("POST", "/api/v1/agents"),
    ("POST", "/api/v1/secrets"),
    ("POST", "/api/v1/models"),
    ("POST", "/api/v1/members/roles:bulk"),
]


def _headers(tenant: uuid.UUID, subject: str, role: str = ORG_ADMIN) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=role)
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def test_the_strongest_role_a_tenant_can_compose_changes_no_configuration() -> None:
    """Compose the maximum, assign it, and try to run the installation with it.

    The token still says `org_admin`, deliberately: on the day this lands that is
    what every human on every live tenant carries, so the refusals below are the
    assignment doing its work and not the absence of a claim.
    """
    tenant = uuid.uuid4()
    admin = _headers(tenant, "die-it-admin")
    strongest = _headers(tenant, "der-maechtigste")

    async with _http() as http:
        # His member row is minted by his first request, exactly as a real
        # employee's is; nothing here creates a person out of band.
        assert (await http.get("/api/v1/me", headers=strongest)).status_code == 200
        members = (await http.get("/api/v1/members", headers=admin)).json()["items"]
        member_id = next(m["id"] for m in members if m["subject"] == "der-maechtigste")

        created = await http.post(
            "/api/v1/roles",
            headers=admin,
            json={
                "name": "Alles was geht",
                "description": "every offerable right there is",
                "permissions": sorted(DELEGATABLE_PERMISSIONS),
            },
        )
        assert created.status_code == 201, created.text

        # 1. The round trip. Not "201 was returned" -- the SET that came back.
        assert set(created.json()["permissions"]) == set(DELEGATABLE_PERMISSIONS), (
            "the writer accepted a set it did not store: "
            f"{sorted(set(created.json()['permissions']) ^ set(DELEGATABLE_PERMISSIONS))}"
        )

        assigned = await http.put(
            f"/api/v1/members/{member_id}/role",
            headers=admin,
            json={"roleId": created.json()["id"]},
        )
        assert assigned.status_code == 200, assigned.text

        # 2. The resolver, read through the screen built to explain it. Both
        #    halves matter: the set is the delegatable set exactly, and it is
        #    STRICTLY smaller than the org_admin his token still names -- so this
        #    also fails if the assignment turned out to be a no-op.
        governance = (await http.get("/api/v1/governance", headers=strongest)).json()
        resolved = set(governance["callerPermissions"])
        assert resolved == set(DELEGATABLE_PERMISSIONS), sorted(
            resolved ^ set(DELEGATABLE_PERMISSIONS)
        )
        assert governance["callerRoleSource"] == "assigned"
        assert governance["callerTenantRoleName"] == "Alles was geht"
        assert resolved < permissions_for(ORG_ADMIN), (
            "the maximum tenant role is not a strict subset of what the token used to give"
        )
        assert not [p for p in resolved if p.endswith(f":{MANAGE}")]

        # 3. The doors. A gate is what decides this, so a gate is what is asked.
        for method, path in _CONFIGURATION_DOORS:
            answer = await http.request(method, path, headers=strongest, json={})
            assert answer.status_code == 403, (
                f"{method} {path} answered {answer.status_code} to the strongest role a "
                f"tenant can build: {answer.text[:200]}"
            )
            # And the same door is open to the administrator, so the 403 above is
            # the ceiling and not a route that refuses everybody.
            control = await http.request(method, path, headers=admin, json={})
            assert control.status_code != 403, (
                f"{method} {path} refuses the administrator too, so it proves nothing "
                "about the ceiling"
            )
