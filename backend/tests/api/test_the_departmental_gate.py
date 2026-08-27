"""`require_departmental` -- the door itself, apart from any route behind it.

§8's list tests the gate through `GET /approvals` and
`POST /approvals/{id}/decision`, which is the right level for "whose approvals do
I see". These are the three things that are true of the GATE and would still be
true if every route behind it were rewritten tomorrow:

* it MINTS the person on first sight, which is the only reason
  `approval_request.decided_by` is never NULL;
* it refuses a principal that is not an operator with a 403 rather than a 500;
* it will not accept a permission that does not exist, at import time.
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
from oc8.api.deps import require_departmental
from oc8.auth import get_identity_provider
from oc8.authz.permissions import APPROVAL_DECIDE
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def _pending(db: Any, tenant: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """(department_id, approval_id) -- one held tool call in one department."""
    department = m.Department(tenant_id=tenant, name="Vertrieb")
    db.add(department)
    await db.flush()
    agent = m.Agent(tenant_id=tenant, department_id=department.id, name="Nora")
    db.add(agent)
    await db.flush()
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
    return department.id, approval.id


async def test_the_first_request_mints_the_person_who_decided(
    app_session: AppSessionFactory,
) -> None:
    """Nobody enrols an administrator, so `upsert=True` is what stops
    `decided_by` from being NULL for the person most likely to decide first.

    With `upsert=False` this request would still succeed -- an `org_admin` is
    admitted by `role_has` and unrestricted by `decide_any` -- and would record a
    decision with nobody behind it. So the assertion that matters is not the 200:
    it is that a row exists afterwards and that the approval names it.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        _department, approval_id = await _pending(db, tenant)

    async with app_session(tenant) as db:
        assert (await db.execute(select(m.OrgMember))).scalars().all() == [], (
            "this tenant has never seen a person; that is the premise"
        )

    token = get_identity_provider().mint(tenant_id=tenant, subject="boss", role="org_admin")
    async with _http() as http:
        got = await http.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"decision": "approve"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert got.status_code == 200, got.text

    async with app_session(tenant) as db:
        minted = (await db.execute(select(m.OrgMember))).scalars().all()
        assert [p.subject for p in minted] == ["boss"], (
            "the gate did not record the person, or recorded them twice"
        )
        row = await db.get(m.ApprovalRequest, approval_id)
        assert row is not None
        assert row.decided_by == minted[0].id
        assert row.status == "approved"

    # And the second request does not mint a second row for the same subject:
    # `uq_org_member_subject` would raise, which reads as a broken endpoint.
    async with app_session(tenant) as db:
        _department, second = await _pending(db, tenant)
    async with _http() as http:
        again = await http.post(
            f"/api/v1/approvals/{second}/decision",
            json={"decision": "reject", "reason": "zu teuer"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert again.status_code == 200, again.text
    async with app_session(tenant) as db:
        assert len((await db.execute(select(m.OrgMember))).scalars().all()) == 1


async def test_a_plugin_token_is_refused_rather_than_crashing_the_route(
    app_session: AppSessionFactory,
) -> None:
    """`scope_for_principal` raises `PermissionError` for a principal that is not
    an operator -- a person is the only thing that stands in a department. Left
    to escape, that is a 500: an authorization refusal that pages somebody gets
    muted, and a muted refusal gets loosened.

    (An `agent` token never reaches here at all; `deny_agent_principals` refuses
    it at the router. A `plugin` token does.)
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        _department, approval_id = await _pending(db, tenant)

    token = get_identity_provider().mint(
        tenant_id=tenant, subject="some-plugin", role="org_admin", kind="plugin"
    )
    async with _http() as http:
        got = await http.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"decision": "approve"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert got.status_code == 403, got.text
    assert "operator" in got.json()["detail"], (
        "the 403 came from somewhere other than the resolver's refusal, so this "
        "test would keep passing if that refusal started 500-ing"
    )

    async with app_session(tenant) as db:
        row = await db.get(m.ApprovalRequest, approval_id)
        assert row is not None and row.status == "pending"
        assert (await db.execute(select(m.OrgMember))).scalars().all() == [], (
            "a plugin token was written into the people table"
        )


def test_a_permission_that_does_not_exist_is_refused_at_import_time() -> None:
    """Same rule as `require_permission`: a typo must not produce a route that
    admits nobody -- including `org_admin`, who holds everything -- because that
    failure looks exactly like a deliberate lockout."""
    with pytest.raises(ValueError):
        require_departmental("aproval:decide")
    # And the control, so this is not passing because the factory rejects
    # everything.
    assert require_departmental(APPROVAL_DECIDE) is not None
