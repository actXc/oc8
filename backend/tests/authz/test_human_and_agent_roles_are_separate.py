"""One table, two populations, and the trap that is live in this repository today.

`pdp.agent_tool_rights` loads an agent's `role` ROW and then resolves it back
through the CODE dictionary **by its NAME**:

    role = await db.get(m.Role, role_id)
    return tool_rights_for_role(role.name)          # pdp.py:409

Before this slice that was harmless, because nobody could write a `role` row --
provisioning wrote five and the seed wrote five and no API existed. This slice
hands a tenant's IT admin a form with a name field in it. From that moment, a
tenant that creates a role literally called `agent_default` and points an
`agent.role_id` at the row has handed that agent `tool:read`, `tool:write` and
`tool:send` -- every right the vocabulary has -- because the name matched a key
in a dictionary.

Three independent guards, and each one is here because the other two can be
removed by somebody who does not know about it:

1. **`role.kind`.** The agent path takes `'agent'` rows only and the human path
   takes `'human'` rows only. This is the one that actually closes it: it works
   against a row written in psql, restored from a backup, or created by an
   importer written next year -- none of which goes through the endpoint.
2. **`POST /roles` refuses a built-in name**, so the row cannot be created
   through the product at all.
3. **`POST /roles` has no `kind` field**, so there is no API by which a tenant
   creates an agent-kind row even if it finds a name nobody reserved.

And in the other direction: a human-kind role must never resolve for an agent,
which is what makes the guard symmetrical rather than a special case for one
name.
"""

from __future__ import annotations

import datetime as dt
import inspect
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sqlalchemy as sa
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.authz.pdp import agent_tool_rights
from oc8.authz.permissions import (
    AGENT_DEFAULT,
    BUILTIN_ROLE_PERMISSIONS,
    DEFAULT_AGENT_TOOL_RIGHTS,
    ORG_ADMIN,
    role_kind,
    tool_rights_for_role,
)
from oc8.main import create_app
from tests.conftest import AppSessionFactory

# No module-level `pytest.mark.asyncio`: this file mixes async doors with
# synchronous source sweeps, and `asyncio_mode = "auto"` already collects the
# async ones.


def _headers(tenant: uuid.UUID, subject: str, role: str) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=role)
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def _agent_on(db: object, tenant: uuid.UUID, role_id: uuid.UUID | None) -> m.Agent:
    agent = m.Agent(
        tenant_id=tenant,
        department_id=uuid.uuid4(),
        name="Nora",
        status="idle",
        role_id=role_id,
        narrowing={},
        definition={},
        presentation={},
    )
    db.add(agent)  # type: ignore[attr-defined]
    await db.flush()  # type: ignore[attr-defined]
    return agent


async def test_a_tenant_role_named_agent_default_is_refused() -> None:
    """409, and independently of the unique index.

    The index cannot be relied on for this: Globex has ONE `role` row, so
    `agent_default` is a free name there, and a fresh tenant provisioned before
    the built-ins were seeded would be the same. The refusal has to be a rule
    about the NAME SET, checked in the service, or the one tenant where the row
    happens to be missing is the one tenant where the trap is open.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        for name in (AGENT_DEFAULT, "AGENT_DEFAULT", " agent_default "):
            created = await http.post(
                "/api/v1/roles",
                json={"name": name, "description": "", "permissions": []},
                headers=_headers(tenant, "boss", ORG_ADMIN),
            )
            assert created.status_code == 409, f"{name!r}: {created.status_code} {created.text}"


async def test_a_tenant_role_is_always_written_as_a_humans(
    app_session: AppSessionFactory,
) -> None:
    """`POST /roles` takes no `kind` field, and sending one changes nothing.

    Tenant-defined AGENT roles are deferred, not forgotten -- "this agent may
    only read" is real and is a second editor gated on `agent:manage`. Until then
    there must be no way at all to reach the agent's population from the human's
    form, including by adding a key to the JSON body.

    Two answers are acceptable and one is not. Ignoring the key (`CamelModel`
    does not forbid extras) and refusing the body outright both leave the agent
    population unreachable; writing `kind='agent'` because the caller asked does
    not, and that is the only outcome asserted against.
    """
    tenant = uuid.uuid4()
    async with _http() as http:
        created = await http.post(
            "/api/v1/roles",
            json={
                "name": "Nur lesen",
                "description": "",
                "permissions": [],
                "kind": "agent",
            },
            headers=_headers(tenant, "boss", ORG_ADMIN),
        )
        assert created.status_code in (201, 422), created.text

    async with app_session(tenant) as db:
        rows = (
            (await db.execute(sa.select(m.Role).where(m.Role.tenant_id == tenant))).scalars().all()
        )
    assert [r.kind for r in rows] in ([], ["human"]), (
        "the request body chose which population to write into"
    )


async def test_an_agent_pointed_at_a_human_kind_role_gets_no_tool_rights(
    app_session: AppSessionFactory,
) -> None:
    """The live trap, reproduced exactly.

    A tenant-created role named `agent_default`, human-kind, holding nothing --
    and an agent pointed at it. Today `tool_rights_for_role(role.name)` looks the
    NAME up in `BUILTIN_ROLE_PERMISSIONS`, finds the agent's own bundle, and
    returns all three rights. The name a tenant typed must not be able to reach
    the agent's vocabulary; only the `kind` column may.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        impostor = m.Role(tenant_id=tenant, name=AGENT_DEFAULT, kind="human", builtin=False)
        db.add(impostor)
        await db.flush()
        agent = await _agent_on(db, tenant, impostor.id)

        granted = await agent_tool_rights(db, agent)

    assert granted == frozenset(), (
        "a role a tenant NAMED `agent_default` handed an agent every tool right "
        "there is; the name is still the discriminator"
    )


async def test_a_soft_deleted_agent_role_grants_no_tool_rights(
    app_session: AppSessionFactory,
) -> None:
    """`SoftDeleteMixin` adds no query filter, so `deleted_at` is load-bearing on
    this path too. A deleted role that still grants is worse than a dangling one:
    the row is gone from every screen and the agent keeps acting."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        role = m.Role(tenant_id=tenant, name=AGENT_DEFAULT, kind="agent", builtin=True)
        db.add(role)
        await db.flush()
        role.deleted_at = dt.datetime.now(tz=dt.UTC)
        await db.flush()
        agent = await _agent_on(db, tenant, role.id)

        assert await agent_tool_rights(db, agent) == frozenset()


async def test_the_agents_own_role_still_grants_all_three(
    app_session: AppSessionFactory,
) -> None:
    """The other half of the guard, and the reason it is not simply "return
    nothing".

    Every seeded ACME agent points at this row (`seed/__init__.py:1004`). If the
    kind check were written the obvious wrong way round -- or if the seed wrote a
    uniform `'human'` -- this is the assertion that fails, instead of every agent
    in the demo silently refusing every tool call for a reason no log line
    mentions a role in.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        role = m.Role(tenant_id=tenant, name=AGENT_DEFAULT, kind="agent", builtin=True)
        db.add(role)
        await db.flush()
        agent = await _agent_on(db, tenant, role.id)

        assert await agent_tool_rights(db, agent) == DEFAULT_AGENT_TOOL_RIGHTS


def test_tool_rights_for_role_is_still_a_pure_function() -> None:
    """Passes today, and that is the point.

    The human resolver is a DIFFERENT function in a DIFFERENT module that
    `pdp.py` does not import. The way that separation dies is somebody making
    this one async and giving it a `db`, "so both paths share the lookup" -- at
    which point the agent path is reading tenant-authored grants and nothing
    anywhere errors. A signature check is the right shape of guard for a seam
    whose violation would be invisible.
    """
    assert inspect.iscoroutinefunction(tool_rights_for_role) is False
    parameters = set(inspect.signature(tool_rights_for_role).parameters)
    assert parameters == {"role"}, (
        f"tool_rights_for_role now takes {sorted(parameters)}; the agent path has grown a database"
    )


def test_a_seeded_tenant_has_exactly_one_agent_kind_role() -> None:
    """Both writers of `role` rows build all five built-ins in one `for` loop
    with no per-name discrimination, so the kind has to be DERIVED from the name
    in one place or it will be uniform in at least one of them.

    Asserted against `role_kind` and `BUILTIN_ROLES` rather than by running the
    seed: `_seed_acme` inserts an organization with the hardcoded, globally
    unique slug `acme`, so it can execute exactly once per database and a second
    caller dies on the ORGANIZATION insert without reaching any assertion. That
    every writer actually calls this is
    `tests/authz/test_every_role_row_names_its_kind.py`; that a real
    `create_tenant` produces four humans and one agent is
    `tests/tenants/test_provision.py`.
    """
    from oc8.seed import BUILTIN_ROLES

    assert [r for r in BUILTIN_ROLES if role_kind(r) == "agent"] == [AGENT_DEFAULT]
    assert set(BUILTIN_ROLES) <= set(BUILTIN_ROLE_PERMISSIONS)
    assert role_kind("Freigabe Vertrieb") == "human", (
        "a name nobody recognises must resolve to the population that grants an "
        "agent nothing; the inverse mistake grants a person's role every tool right"
    )
