"""Nobody reads a person's authority off their token any more.

`org_member.role_id` is an OVERRIDE. The moment that is true, every surviving
`role_has(principal.role, ...)` is a place where a demoted administrator keeps
what the administrator took away -- and each one is a different screen, so no
single test at a single door can find them all.

There were five when this slice began, and they are not equivalent:

* `api/deps.py` (two) -- the gates themselves. The design converts both, and
  without them the feature does not exist at all.
* `api/v1/channels.py` -- `POST /channels/{channel}/link` reads
  `approval:decide_any` off the token and, if it is there, writes
  `org_member.all_departments = True`. That is a DURABLE ROW granting
  company-wide decide authority, minted from a claim the assignment was supposed
  to have replaced -- and it outlives the role, so revoking the role afterwards
  does not take it back. The worst of the five.
* `approvals/service.py` -- `_may_apply_the_effect` asks whether the actor holds
  `budget:manage` / `agent:manage` before applying an approval's effect. This is
  the check the previous slice added after a seat-holder lifted his own
  department's token cap. Read off the token, a demoted admin walks straight
  back through it.
* `realtime/ws.py` -- the operator feed, gated on `run:view`. A WebSocket route
  cannot carry an HTTP dependency, so this one genuinely needs its own answer
  rather than a conversion; it needs to be a DECIDED answer.

So: a source sweep, plus one end-to-end proof at the door where being wrong costs
money. The sweep is the forcing function -- the next gate somebody writes will
reach for `principal.role`, because that is the obvious move and because several
functions already did -- and the behavioural test is what stops the sweep being
satisfied by moving a call rather than fixing it.
"""

from __future__ import annotations

import ast
import pathlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.authz.permissions import (
    APPROVAL,
    APPROVAL_DECIDE,
    CLARIFICATION_ANSWER,
    CLARIFICATION_VIEW,
    ORG_ADMIN,
    VIEW,
    perm,
)
from oc8.authz.scope import subject_uuid_for
from oc8.main import create_app
from tests.conftest import AppSessionFactory

# No module-level `pytest.mark.asyncio`: this file mixes async doors with
# synchronous source sweeps, and `asyncio_mode = "auto"` already collects the
# async ones.

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "oc8"

#: The two functions that answer "what does this ROLE NAME grant" out of the code
#: table. Neither can see a tenant-defined role -- that is the seam
#: `test_permissions_for_still_cannot_see_a_tenant_role` protects -- so calling
#: either on a principal's role is, after this slice, a read of the wrong thing.
_CODE_ONLY_RESOLVERS = {"role_has", "permissions_for"}

#: Where reading the code table off a principal is still correct.
#:
#: `authz/` is where the resolver lives and where the code table IS the answer
#: (it is the floor a caller without an assignment falls back to). `governance.py`
#: is the screen that explains a refusal: it renders the built-in model itself,
#: and gating or rewriting that would re-open the bug 12c40e6 records.
_ALLOWED = ("authz/", "api/v1/governance.py")


def _reads_a_principals_role(node: ast.AST) -> bool:
    """`role_has(principal.role, ...)` / `permissions_for(principal.role)`.

    AST rather than a grep: `approvals/service.py` spells it
    `role_has(actor.principal.role, required)`, and any regex tight enough to
    avoid false positives would have missed exactly that one.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    if name not in _CODE_ONLY_RESOLVERS or not node.args:
        return False
    first = node.args[0]
    return isinstance(first, ast.Attribute) and first.attr == "role"


def _sites() -> list[str]:
    found: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        relative = str(path.relative_to(_SRC)).replace("\\", "/")
        if any(relative.startswith(allowed) for allowed in _ALLOWED):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _reads_a_principals_role(node):
                found.append(f"oc8/{relative}:{node.lineno}")
    return found


def test_the_sweep_actually_recognises_both_shapes() -> None:
    """A matcher that matched nothing would pass the test below it for ever --
    and it would do so silently on the day the four real sites are fixed, which
    is exactly when nothing else is watching.

    Both spellings, because they are genuinely different trees:
    `role_has(principal.role, ...)` and `approvals/service.py`'s
    `role_has(actor.principal.role, required)`. Any regex tight enough to avoid
    false positives missed the second one.
    """
    probe = ast.parse(
        "role_has(principal.role, p)\n"
        "role_has(actor.principal.role, required)\n"
        "permissions_for(principal.role)\n"
        "permissions_for(role.name)\n"  # NOT a hit: that is the role ROW's name
    )
    assert sum(1 for n in ast.walk(probe) if _reads_a_principals_role(n)) == 3
    assert _SRC.is_dir() and len(list(_SRC.rglob("*.py"))) > 50, "the tree walk is broken"


def test_no_module_outside_authz_reads_a_principals_role_directly() -> None:
    """The read term's forcing function.

    Every hit is a place where the token still decides, i.e. a place an
    assignment does not reach. Fix it by going through
    `authority_for_principal`, or -- for the WebSocket, which cannot carry an
    HTTP dependency -- by writing the resolver call in place. Adding the file to
    `_ALLOWED` is also an answer, and it costs a sentence saying why the code
    table is the right source there.
    """
    sites = _sites()
    assert not sites, (
        f"{len(sites)} site(s) still answer 'what may this person do' from the "
        f"token alone, so an assigned role does not reach them: {sites}"
    )


# ------------------------------------------------- and one proof at a real door


def _headers(tenant: uuid.UUID, subject: str, role: str) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=role)
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def test_a_demoted_administrator_cannot_apply_a_management_effect(
    app_session: AppSessionFactory,
) -> None:
    """The end-to-end version of the sweep, at the door that costs money.

    `EFFECT_PERMISSIONS["budget_incident"]` is `budget:manage` -- approving one
    sets `override_until` and un-pauses the frozen scope, which is the same act
    as `PUT /budgets`. The previous slice added that check after a seat-holder
    used it to lift his own department's cap, and it asks `role_has(actor.
    principal.role, ...)`.

    Anna's token says `org_admin`, as every token on every live tenant does. Her
    administrator has assigned her a role that holds four permissions and no
    `:manage` at all. If the effect gate still reads the token she approves it,
    and the assertion that matters is not the status code -- it is that
    `override_until` is still NULL afterwards, because a route that applies the
    effect and then refuses passes a status-code-only test with the month's cap
    already lifted.
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

        role = m.Role(tenant_id=tenant, name="Freigabe Vertrieb", kind="human")
        db.add(role)
        await db.flush()
        for permission in (
            perm(APPROVAL, VIEW),
            APPROVAL_DECIDE,
            CLARIFICATION_VIEW,
            CLARIFICATION_ANSWER,
        ):
            db.add(m.RolePermission(tenant_id=tenant, role_id=role.id, permission=permission))
        member = m.OrgMember(
            tenant_id=tenant,
            subject=subject,
            subject_uuid=subject_uuid_for(subject),
            display_name="Anna",
            role_id=role.id,
        )
        db.add(member)
        await db.flush()
        # A SEAT as well as the role, and it is what makes this test about the
        # effect gate at all. Role says WHAT and seat says WHERE, so a person
        # with a role and no seat anywhere sees no department's approvals: she
        # would be refused 404 by `approvals/repo.load_for_actor` one door EARLIER
        # than the gate this test is written about, and the assertion below would
        # pass on the wrong refusal.
        db.add(
            m.OrgMemberDepartment(
                tenant_id=tenant,
                member_id=member.id,
                department_id=dept.id,
                seat_role="dept_approver",
            )
        )
        await db.flush()
        incident_id, department_id = incident.id, dept.id

    headers = _headers(tenant, subject, ORG_ADMIN)
    async with _http() as http:
        # The control, and it is the whole argument: her assignment has already
        # taken `budget:manage` away at the ordinary door.
        assert (await http.get("/api/v1/budgets", headers=headers)).status_code == 403

        refused = await http.post(
            f"/api/v1/approvals/{incident_id}/decision",
            json={"decision": "approve"},
            headers=headers,
        )
        assert refused.status_code == 403, refused.text
        assert "budget:manage" in refused.text

    async with app_session(tenant) as db:
        budget = (
            await db.execute(select(m.Budget).where(m.Budget.department_id == department_id))
        ).scalar_one()
        row = await db.get(m.ApprovalRequest, incident_id)
    assert budget.override_until is None, (
        "a demoted administrator lifted the department's token cap through an "
        "approval; the effect gate is still reading the token"
    )
    assert row is not None and row.status == "pending"
