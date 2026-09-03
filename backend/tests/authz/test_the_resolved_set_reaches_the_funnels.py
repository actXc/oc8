"""The resolver is not finished at the gate: three places behind it decide too.

`test_the_authority_seam_is_the_only_seam` proves nobody OUTSIDE `authz/` reads a
principal's role any more. That is a sweep, and a sweep can be satisfied by
moving a call. These are the three conversions it forced, each asserted where
being wrong costs something, plus the duplication the conversion introduced and
no longer has.

* **The effect gate** (`approvals/service._may_apply_the_effect`). Approving a
  `budget_incident` lifts a department's token cap for the month -- the same act
  as `PUT /budgets`, which is `budget:manage`. A seat-holder used it to lift his
  own cap once already; this is the version where the caller's token says
  `org_admin` and his assignment says otherwise.
* **The link route** (`api/v1/channels.create_link_code`). It writes
  `org_member.all_departments = True`, a DURABLE row granting company-wide decide
  authority that outlives whatever authorised it. Every other conversion in this
  slice produces a wrong ANSWER when it is wrong; this one produces a wrong ROW,
  and revoking the role afterwards does not take it back.
* **The socket** (`realtime/ws.py`). It carries the activity feed as free text and
  cannot carry an HTTP dependency, so it is the one place the resolver is called
  by hand.

And the duplication: `Authority.by_department` and `DepartmentScope` read the
same seats, one joined onto the member lookup and one standing on its own for
the messenger door. Two readers of one table is how a door and a filter come to
disagree -- and the answer to that is one reader, not two held to the same
answer. The authority's copy was read by nothing in `src/` and cost a
three-table LEFT JOIN on all 110 permission-gated requests; it is gone, and the
last test here is what keeps it gone.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from starlette.requests import Request

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.auth.principal import Principal
from oc8.authz.authority import authority_for_principal, resolve_authority
from oc8.authz.permissions import (
    APPROVAL,
    APPROVAL_DECIDE,
    BUDGET,
    CLARIFICATION_ANSWER,
    CLARIFICATION_VIEW,
    MANAGE,
    ORG_ADMIN,
    SEAT_APPROVER,
    SEAT_VIEWER,
    VIEW,
    perm,
)
from oc8.authz.scope import scope_for_principal, subject_uuid_for
from oc8.main import create_app
from tests.conftest import AppSessionFactory

# No module-level `pytest.mark.asyncio`: this file mixes async doors with one
# synchronous source assertion, and `asyncio_mode = "auto"` already collects the
# async ones.

APPROVAL_VIEW = perm(APPROVAL, VIEW)
BUDGET_MANAGE = perm(BUDGET, MANAGE)

#: What an IT admin would actually compose: sign off offers, answer questions,
#: and change no configuration anywhere. No `:manage` at all -- there cannot be
#: one, every `:manage` is `NEVER_DELEGATABLE`.
FREIGABE = (APPROVAL_VIEW, APPROVAL_DECIDE, CLARIFICATION_VIEW, CLARIFICATION_ANSWER)

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


def _request() -> Request:
    return Request(
        {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}
    )


@contextmanager
def _statements() -> Iterator[list[str]]:
    """Every SQL statement issued while this is open.

    On the `Engine` class rather than on one instance: `app_session` builds a
    throwaway engine per call, so an instance-level listener would have nothing
    to attach to.
    """
    seen: list[str] = []

    def _record(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", _record)


async def _demoted(
    db: Any, tenant: uuid.UUID, subject: str, *, permissions: tuple[str, ...] = FREIGABE
) -> m.OrgMember:
    """Somebody whose token says `org_admin` -- as every token on every live
    tenant does -- and whose administrator has said otherwise."""
    role = m.Role(tenant_id=tenant, name=f"Freigabe {subject}", kind="human")
    db.add(role)
    await db.flush()
    for permission in permissions:
        db.add(m.RolePermission(tenant_id=tenant, role_id=role.id, permission=permission))
    member = m.OrgMember(
        tenant_id=tenant,
        subject=subject,
        subject_uuid=subject_uuid_for(subject),
        display_name=subject,
        role_id=role.id,
    )
    db.add(member)
    await db.flush()
    return member


# ----------------------------------------------------------- the effect gate


async def test_a_demoted_administrator_with_a_seat_is_refused_the_management_effect(
    app_session: AppSessionFactory,
) -> None:
    """Anna can SEE this approval and is still refused, which is the only
    arrangement that tests the effect gate at all.

    A demotion without a seat is refused one door earlier -- `load_for_actor`
    404s, because a role says WHAT and a seat says WHERE and she has no seat. So
    she is given a `dept_approver` seat in the department the incident belongs
    to: she is admitted at `require_departmental(approval:decide)`, the row loads,
    `may_decide` says yes, and the ONLY thing between her and the month's token
    cap is `_may_apply_the_effect` asking what she actually holds.

    The status code is not the assertion that matters. `override_until` is: a
    funnel that applied the effect and then refused would pass a status-code test
    with the cap already lifted.
    """
    tenant = uuid.uuid4()
    subject = f"anna-{uuid.uuid4()}"
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb")
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Nora")
        db.add(agent)
        db.add(m.Budget(tenant_id=tenant, department_id=dept.id, hard_limit_tokens=1000))
        await db.flush()
        incident = m.ApprovalRequest(
            tenant_id=tenant,
            agent_id=agent.id,
            department_id=dept.id,
            action_type="budget_incident",
            status="pending",
            title="Token-Budget überschritten",
            detail="",
            payload={"scope": "department", "department_id": str(dept.id)},
        )
        db.add(incident)
        member = await _demoted(db, tenant, subject)
        db.add(
            m.OrgMemberDepartment(
                tenant_id=tenant,
                member_id=member.id,
                department_id=dept.id,
                seat_role=SEAT_APPROVER,
            )
        )
        await db.flush()
        incident_id, department_id = incident.id, dept.id

    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=ORG_ADMIN)
    headers = {"Authorization": f"Bearer {token}"}
    async with _http() as http:
        # The control: her assignment has already taken `budget:manage` away at
        # the ordinary door, on a token that says `org_admin`.
        assert (await http.get("/api/v1/budgets", headers=headers)).status_code == 403
        # And she is genuinely at the decision door, not refused before it.
        listed = await http.get("/api/v1/approvals", headers=headers)
        assert listed.status_code == 200
        assert [row["id"] for row in listed.json()] == [str(incident_id)]

        refused = await http.post(
            f"/api/v1/approvals/{incident_id}/decision",
            json={"decision": "approve"},
            headers=headers,
        )
    assert refused.status_code == 403, refused.text
    assert BUDGET_MANAGE in refused.text

    async with app_session(tenant) as db:
        budget = (
            await db.execute(select(m.Budget).where(m.Budget.department_id == department_id))
        ).scalar_one()
        row = await db.get(m.ApprovalRequest, incident_id)
    assert budget.override_until is None, (
        "a demoted administrator lifted the department's token cap through an "
        "approval he was allowed to decide; the effect gate is reading the token"
    )
    assert row is not None and row.status == "pending"


async def test_the_same_seat_holder_still_decides_what_costs_no_management_right(
    app_session: AppSessionFactory,
) -> None:
    """The guard against fixing the effect gate by breaking the workspace.

    `EFFECT_PERMISSIONS["tool_send"]` is `None` -- a seat is enough, and it is the
    3000-EUR gate the whole product is demonstrated on. If the conversion had made
    the effect gate answer "no" for everybody without a token claim, this slice
    would have shipped a demotion that silently emptied the flagship screen.
    """
    tenant = uuid.uuid4()
    subject = f"anna-{uuid.uuid4()}"
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb")
        db.add(dept)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=dept.id, name="Nora")
        db.add(agent)
        await db.flush()
        held = m.ApprovalRequest(
            tenant_id=tenant,
            agent_id=agent.id,
            department_id=dept.id,
            action_type="tool_send",
            status="pending",
            title="Angebot 4.320 EUR",
            detail="",
        )
        db.add(held)
        member = await _demoted(db, tenant, subject)
        db.add(
            m.OrgMemberDepartment(
                tenant_id=tenant,
                member_id=member.id,
                department_id=dept.id,
                seat_role=SEAT_APPROVER,
            )
        )
        await db.flush()
        held_id = held.id

    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=ORG_ADMIN)
    async with _http() as http:
        decided = await http.post(
            f"/api/v1/approvals/{held_id}/decision",
            json={"decision": "approve"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert decided.status_code == 200, decided.text

    async with app_session(tenant) as db:
        row = await db.get(m.ApprovalRequest, held_id)
    assert row is not None and row.status == "approved"


# ------------------------------------------------------------- the link route


async def test_a_demoted_administrator_cannot_mint_himself_company_wide_authority(
    app_session: AppSessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worst of the five sites, because it is the only one that leaves a row.

    `POST /channels/{channel}/link` writes `all_departments = True` for a caller
    holding `approval:decide_any`. Read off the token that claim is Anna's
    forever: her assignment took it away, the link route hands it back as a
    durable grant, and revoking the role afterwards does not touch it -- she would
    decide every department's approvals from her phone with an authority nothing
    on any screen explains.

    `channel:manage` is `NEVER_DELEGATABLE`, so she cannot reach this route
    through HTTP at all once she is demoted -- which is itself the first half of
    the answer and is asserted. The body is then called directly for the second
    half: if the route is ever reached by a caller whose token is richer than his
    assignment, the grant must still not happen.
    """

    async def _channels(_db: Any, *, tenant_id: uuid.UUID) -> dict[str, Any]:
        return {CHANNEL: _FakeChannel()}

    monkeypatch.setattr("oc8.api.v1.channels.channels_for_tenant", _channels)
    from oc8.api.v1.channels import create_link_code

    tenant = uuid.uuid4()
    subject = f"anna-{uuid.uuid4()}"
    async with app_session(tenant) as db:
        await _demoted(db, tenant, subject)

    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=ORG_ADMIN)
    async with _http() as http:
        refused = await http.post(
            f"/api/v1/channels/{CHANNEL}/link", headers={"Authorization": f"Bearer {token}"}
        )
    assert refused.status_code == 403, refused.text

    async with app_session(tenant) as db:
        # This commits (the route does), which unbinds `app.tenant_id` for the
        # rest of THIS session -- hence the fresh one below.
        await create_link_code(
            CHANNEL, db, Principal(subject=subject, tenant_id=tenant, role=ORG_ADMIN)
        )

    async with app_session(tenant) as db:
        member = (
            await db.execute(select(m.OrgMember).where(m.OrgMember.subject == subject))
        ).scalar_one()
        granted = (
            (
                await db.execute(
                    select(m.AuditEvent).where(
                        m.AuditEvent.action == "member.all_departments_granted"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert member.all_departments is False, (
        "a demoted administrator was written company-wide decide authority from a "
        "claim his assignment had already replaced -- and the row outlives the role"
    )
    assert granted == [], "an audit event for a grant that must not have happened"


# ----------------------------------------------------------------- the socket


def test_the_socket_asks_the_resolver_and_not_the_token() -> None:
    """A source-level assertion, and the only kind available here.

    The socket is the one gate that cannot be a dependency, so it is the one gate
    that can silently keep its own copy of the answer. `run:view` is
    `NOT_YET_DELEGATABLE`, so no tenant-defined role can grant it back -- meaning
    a demoted administrator holds it nowhere and must be refused a feed that ships
    `activity.logged` with `message` and `detail` verbatim.

    Behaviour is covered by `tests/realtime/test_the_socket_is_gated_like_the_feed`,
    which this must not weaken: every role that may read the feed over HTTP still
    connects.
    """
    import inspect

    from oc8.realtime import ws

    source = inspect.getsource(ws.events_ws)
    assert "resolve_authority" in source
    assert "role_has" not in source


# ------------------------------------------- and the duplication it did NOT keep


async def test_there_is_exactly_one_reader_of_a_seat_on_the_authorization_path(
    app_session: AppSessionFactory,
) -> None:
    """`DepartmentScope` is the only thing that reads `org_member_department`.

    For one release there were two. `Authority` carried a `by_department` mapping
    and a `holds_anywhere` built from it -- a second implementation of the seat
    term, filled by a three-table LEFT JOIN on every one of the 110
    permission-gated requests, read by no module in `src/`, and therefore
    exercised by no test at the door it claimed to be the term of. Two readers of
    one table is how a door and a filter come to disagree; the fix was not to
    hold them to the same answer but to have one of them.

    So this asserts BOTH halves, and the second is the one that rots: the scope
    still applies every filter (a viewer, an approver, a REVOKED approver which
    kept deciding once, and a seat in an ARCHIVED department which granted a
    place to stand no screen could show), and the resolver still issues no
    statement that touches a seat at all.
    """
    tenant = uuid.uuid4()
    subject = f"hos-{uuid.uuid4()}"
    async with app_session(tenant) as db:
        names = ("Vertrieb", "Entwicklung", "Einkauf", "Archiviert")
        departments = [m.Department(tenant_id=tenant, name=name) for name in names]
        for department in departments:
            db.add(department)
        await db.flush()
        sales, engineering, purchasing, archived = departments

        member = m.OrgMember(
            tenant_id=tenant,
            subject=subject,
            subject_uuid=subject_uuid_for(subject),
            display_name="Head of Sales",
        )
        db.add(member)
        await db.flush()
        for department, seat_role in (
            (sales, SEAT_APPROVER),
            (engineering, SEAT_VIEWER),
            (purchasing, SEAT_APPROVER),
            (archived, SEAT_APPROVER),
        ):
            db.add(
                m.OrgMemberDepartment(
                    tenant_id=tenant,
                    member_id=member.id,
                    department_id=department.id,
                    seat_role=seat_role,
                )
            )
        await db.flush()

        revoked = (
            await db.execute(
                select(m.OrgMemberDepartment).where(
                    m.OrgMemberDepartment.department_id == purchasing.id
                )
            )
        ).scalar_one()
        revoked.revoked_at = dt.datetime.now(tz=dt.UTC)
        archived.deleted_at = revoked.revoked_at
        await db.flush()

        principal = Principal(subject=subject, tenant_id=tenant, role="member")
        with _statements() as resolver_issued:
            authority = await authority_for_principal(_request(), db, principal)
        _member, scope = await scope_for_principal(db, principal)
        sales_id, engineering_id = sales.id, engineering.id

    assert scope.viewable == {sales_id, engineering_id}, (
        "the seat read kept a revoked seat or a seat in an archived department; "
        "the gate admits somebody the queue then shows nothing to"
    )
    assert scope.may_decide(sales_id) is True
    assert scope.may_decide(engineering_id) is False
    assert scope.holds_anywhere(APPROVAL_DECIDE) is True
    assert scope.holds_anywhere(perm(BUDGET, VIEW)) is False, (
        "a permission outside the seat vocabulary was answered from a seat"
    )

    assert not hasattr(authority, "by_department"), (
        "the resolver grew a second seat map; the door and the row filter can "
        "now disagree about which departments somebody sits in"
    )
    assert not hasattr(authority, "holds_anywhere"), (
        "the resolver grew a second implementation of the seat term, and no door "
        "calls it -- so nothing tests it"
    )
    seat_reads = [s for s in resolver_issued if "org_member_department" in s]
    assert not seat_reads, (
        f"the resolver read the seat tables on a permission-gated request: {seat_reads}"
    )


async def test_the_resolver_and_the_scope_agree_that_a_demotion_is_not_unrestricted(
    app_session: AppSessionFactory,
) -> None:
    """Both doors, one rule.

    `Authority.unrestricted` gates `require_departmental`; `DepartmentScope`'s
    gates the ROWS `approvals/repo.py` returns. If only the gate had been
    converted, a demoted administrator would be refused at the door and still
    have every department's approvals in the list the moment any other route let
    him in -- and if only the scope had, the reverse. They are computed from the
    same resolved set, and this is what says so.
    """
    tenant = uuid.uuid4()
    subject = f"anna-{uuid.uuid4()}"
    async with app_session(tenant) as db:
        await _demoted(db, tenant, subject)
        principal = Principal(subject=subject, tenant_id=tenant, role=ORG_ADMIN)
        authority = await resolve_authority(db, principal)
        _member, scope = await scope_for_principal(db, principal)

    assert authority.unrestricted is False
    assert authority.decides_everywhere is False
    assert scope.is_unrestricted is False, (
        "the scope is still unrestricted for a demoted administrator; the demotion "
        "applies to every screen except the one that lists what releases money"
    )
    assert scope.decides_everywhere is False
    assert scope.is_empty is True
