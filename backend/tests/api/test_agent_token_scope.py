"""An agent token belongs in two places, and the operator API is not one of them.

A run-scoped `kind=agent` token is minted for a container and travels INTO it.
Its `run:<id>` scope is enforced by the LLM gateway and the tool gateway -- and
by NOTHING else: every other route treated it as an ordinary tenant principal.

That was not theoretical. Verified against the running stack on 2026-07-27: the
exact token the nanoclaw runtime hands a container was accepted by
`POST /api/v1/approvals/{id}/decision` with 200 OK on its own pending
`tool_send` approval, and the audit event then recorded `actor_type="operator"`.
The container has a shell and can reach the backend, so one prompt injection in
a CRM record would have ended the >3000 EUR gate the whole design exists to
enforce.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from oc8.runtime.states import RunState
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

#: The one prefix under /api/v1 an agent token may still reach: the internal API
#: the reference shell drives, which does its own run-scope check.
INTERNAL_PREFIX = "/api/v1/internal/agent"


def _agent_token(tenant: uuid.UUID, agent_id: uuid.UUID, run_id: uuid.UUID) -> str:
    return get_identity_provider().mint(
        tenant_id=tenant, subject=f"agent:{agent_id}", role="agent_default",
        kind="agent", scopes=[f"run:{run_id}"],
    )


async def _seed(db: Any, tenant: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    dept = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
    db.add(dept)
    await db.flush()
    agent = m.Agent(
        tenant_id=tenant, department_id=dept.id, name="Nora", status="running",
        narrowing={}, definition={}, presentation={},
    )
    db.add(agent)
    await db.flush()
    run = m.AgentRun(tenant_id=tenant, agent_id=agent.id, state=RunState.RUNNING.value, context={})
    db.add(run)
    await db.flush()
    approval = m.ApprovalRequest(
        tenant_id=tenant, agent_id=agent.id, action_type="tool_send", status="pending",
        title="Nora wants to call create_record", detail="value €7500 meets threshold €3000",
        payload={"tool": "create_record", "arguments": {"model": "crm.lead"}},
    )
    db.add(approval)
    await db.flush()
    return agent.id, run.id, approval.id


async def test_an_agent_token_cannot_approve_its_own_action(
    app_session: AppSessionFactory,
) -> None:
    """The finding itself: the container holds this token, and approving with it
    would let an agent lift its own spending gate."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, run_id, approval_id = await _seed(db, tenant)

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                f"/api/v1/approvals/{approval_id}/decision",
                json={"decision": "approve"},
                headers={"Authorization": f"Bearer {_agent_token(tenant, agent_id, run_id)}"},
            )

    assert r.status_code == 403, r.text

    async with app_session(tenant) as db:
        approval = await db.get(m.ApprovalRequest, approval_id)
        assert approval is not None and approval.status == "pending", "and nothing was decided"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/approvals", None),
        ("GET", "/api/v1/agents", None),
        ("GET", "/api/v1/secrets", None),
        ("GET", "/api/v1/budgets", None),
        ("GET", "/api/v1/audit", None),
    ],
)
async def test_the_operator_api_refuses_an_agent_token(
    app_session: AppSessionFactory, method: str, path: str, body: dict[str, Any] | None
) -> None:
    """Not only the endpoint that was found: reading a tenant's approvals, agents,
    secrets, budgets or audit trail from inside a container is the same class of
    hole, and it is the class that gets forgotten one endpoint at a time."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        agent_id, run_id, _approval = await _seed(db, tenant)

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.request(
                method, path, json=body,
                headers={"Authorization": f"Bearer {_agent_token(tenant, agent_id, run_id)}"},
            )

    assert r.status_code == 403, f"{method} {path} answered {r.status_code}"


def _paths(routes: list[Any]) -> list[str]:
    """Every path under `routes`, however deeply routers are nested.

    `api_router` includes sub-routers, which are themselves nested objects rather
    than flattened routes -- so a single level of walking hits an object with no
    `.path` at all.
    """
    out: list[str] = []
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            out.extend(_paths(list(original.routes)))
        elif hasattr(route, "path"):
            out.append(route.path)
    return out


def _included_routers(app: Any) -> list[tuple[str, list[Any], list[str]]]:
    """(prefix, sub-routes, guard names) for every router mounted on `app`.

    This FastAPI version keeps an included router as ONE nested object instead of
    flattening its routes into `app.routes` -- so a naive walk of `app.routes`
    finds no /api/v1 route at all and any assertion over it passes vacuously.
    That is exactly how a security check can look green while testing nothing, so
    the caller asserts that this found something.
    """
    found = []
    for route in app.routes:
        context = getattr(route, "include_context", None)
        original = getattr(route, "original_router", None)
        if context is None or original is None:
            continue
        guards = [
            getattr(getattr(d, "dependency", None) or getattr(d, "call", None), "__name__", "")
            for d in (getattr(context, "dependencies", None) or [])
        ]
        found.append((getattr(context, "prefix", ""), list(original.routes), guards))
    return found


async def test_every_operator_router_carries_the_guard() -> None:
    """The regression net for routes that do not exist yet.

    Checking a handful of endpoints by hand is how this hole survived in the
    first place: a router added next month would simply not be in anyone's list.
    """
    app = create_app()
    routers = _included_routers(app)
    assert routers, "found no mounted routers -- this test would otherwise pass vacuously"

    unguarded = [
        f"{prefix} ({len(routes)} routes)"
        for prefix, routes, guards in routers
        if prefix.startswith("/api/v1")
        and not any(path.startswith("/internal/agent") for path in _paths(routes))
        # The realtime feed is a WebSocket, which cannot carry an HTTP dependency;
        # it refuses agent tokens in its own handler instead, and
        # tests/realtime/test_ws.py::test_ws_rejects_an_agent_token pins that.
        and not any(path.startswith("/events/ws") for path in _paths(routes))
        and "deny_agent_principals" not in guards
    ]

    assert not unguarded, "operator routers reachable with an agent token: " + ", ".join(unguarded)


async def test_the_internal_agent_api_is_exempt_from_the_guard() -> None:
    """The exception that must keep working: the reference shell drives its run
    through this path with exactly this token, so a blanket 403 here would break
    the isolated runtime instead of protecting anything.

    Asserted structurally rather than by calling `/step`: that endpoint really
    runs a model turn, so a behavioural check here would be testing the provider's
    reachability. The behaviour is already covered by tests/api/test_internal_agent.py,
    which drives this path with an agent token end to end."""
    app = create_app()
    internal = [
        (prefix, routes, guards)
        for prefix, routes, guards in _included_routers(app)
        if any(path.startswith("/internal/agent") for path in _paths(routes))
    ]

    assert internal, "the internal agent API is mounted"
    for prefix, _routes, guards in internal:
        assert "deny_agent_principals" not in guards, (
            f"{prefix} would refuse the very runtime it exists for"
        )
