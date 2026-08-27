"""`metering/budget.py` is the one caller allowed to say where an approval belongs.

`tests/approvals/test_department_is_pinned_at_raise.py` proves it is the ONLY
module that passes `department_id=` -- by sweeping the source for the keyword.
That sweep cannot see what it passes, so every one of these is still green if
the argument is wrong:

    department_id=None                       # a department breach filed company-wide
    department_id=breaching_agent.department_id   # a TENANT breach filed on one team
    department_id=DERIVE                     # same, by another spelling

Each of those is a real failure with no error behind it. A department breach
filed company-wide never reaches the approver of the department that spent the
money, and a tenant breach filed under the breaching agent's department hides a
company-wide freeze -- every agent in the tenant is paused -- inside one team's
queue, where the only person who can lift it may not be looking.

Not in the design's §8 list. Written because the change that motivated it
(replacing the hand-built `ApprovalRequest(...)` at `budget.py:284` with a
`raise_approval` call) is exactly the kind of edit whose argument list nothing
was checking.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.approvals import raise_approval
from oc8.metering import set_budget, trigger_budget_hard_stop
from oc8.metering.usage import record_usage
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def _breach(
    db: AsyncSession, tenant: uuid.UUID, department_id: uuid.UUID, *, scope: uuid.UUID | None
) -> m.Agent:
    """Spend past the hard limit of `scope` with an agent standing in `department_id`."""
    await set_budget(
        db,
        tenant_id=tenant,
        department_id=scope,
        soft_limit_tokens=1,
        hard_limit_tokens=10,
    )
    agent = m.Agent(tenant_id=tenant, department_id=department_id, name="Spender", status="running")
    db.add(agent)
    await db.flush()
    await record_usage(
        db,
        tenant_id=tenant,
        department_id=department_id,
        agent_id=agent.id,
        request_id=uuid.uuid4(),
        model="m",
        provider="p",
        tokens_in=500,
        tokens_out=0,
    )
    return agent


async def test_a_department_breach_lands_in_that_departments_queue(
    app_session: AppSessionFactory,
) -> None:
    """The department that spent it is the department that must answer for it."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = m.Department(tenant_id=tenant, name="Vertrieb")
        db.add(sales)
        await db.flush()

        agent = await _breach(db, tenant, sales.id, scope=sales.id)
        incident = await trigger_budget_hard_stop(db, tenant_id=tenant, breaching_agent=agent)

        assert incident is not None
        assert incident.department_id == sales.id, (
            "a department's budget breach was filed company-wide, so the approver "
            "of the department whose agents are now frozen never sees it"
        )
        # And it is really on the row, not just on the in-memory object.
        reloaded = (
            await db.execute(
                select(m.ApprovalRequest.department_id).where(m.ApprovalRequest.id == incident.id)
            )
        ).scalar_one()
        assert reloaded == sales.id


async def test_a_tenant_breach_is_company_wide_even_though_one_agent_caused_it(
    app_session: AppSessionFactory,
) -> None:
    """NULL is not "we did not know" here -- it is the answer.

    The breaching agent stands in Vertrieb and every agent in the tenant is now
    frozen. Filing this under Vertrieb would put the company's problem in one
    team's queue; only the unrestricted may see and lift it.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = m.Department(tenant_id=tenant, name="Vertrieb")
        db.add(sales)
        await db.flush()

        agent = await _breach(db, tenant, sales.id, scope=None)
        incident = await trigger_budget_hard_stop(db, tenant_id=tenant, breaching_agent=agent)

        assert incident is not None
        assert incident.department_id is None, (
            "a tenant-wide freeze was filed under the breaching agent's department, "
            "where the only people who can lift it may never look"
        )
        # The payload's `scope` and the column must agree: `resolve_budget_incident`
        # reads the payload to decide WHAT to unfreeze, the queue reads the column
        # to decide WHO may say so. Two answers to "which scope" is one too many.
        assert (incident.payload or {}).get("scope") == "tenant"


async def test_an_approval_whose_agent_cannot_be_read_is_not_filed_anywhere(
    app_session: AppSessionFactory,
) -> None:
    """`agent_id` carries no foreign key, so the derivation can find nothing.

    Two ways to get this wrong, and both are worse than what is asserted here.
    Raising would take down a tool call that only wanted to ask a human -- the
    agent's run dies because of a bad id on the row it was about to write.
    Falling back to "the caller's department" (there isn't one) or to some
    default would file the approval under a department, where a seat-holder
    would be shown a decision about work he has no way to check.

    NULL is the fail-CLOSED direction: tenant-wide, so only the unrestricted
    ever sees it. Not in the design's §8 list; the branch exists because
    `agent_id` is unconstrained and someone eventually passes a stale one.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        approval = await raise_approval(
            db,
            tenant_id=tenant,
            agent_id=uuid.uuid4(),  # no such agent
            action_type="tool_send",
            title="Angebot",
        )
        assert approval.department_id is None


async def test_the_incident_still_carries_everything_the_screen_renders(
    app_session: AppSessionFactory,
) -> None:
    """Going through `raise_approval` must not quietly drop a field.

    The funnel names its own `status`, so the hand-built row's `status="pending"`
    had to go; `title`, `detail` and `amount_text` are passed through and are the
    whole of what an operator reads before unfreezing a company.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = m.Department(tenant_id=tenant, name="Vertrieb")
        db.add(sales)
        await db.flush()

        agent = await _breach(db, tenant, sales.id, scope=None)
        incident = await trigger_budget_hard_stop(db, tenant_id=tenant, breaching_agent=agent)

        assert incident is not None
        assert incident.status == "pending"
        assert incident.action_type == "budget_incident"
        assert incident.title == "Token budget exceeded — tenant"
        assert "Runs are paused" in incident.detail
        assert incident.amount_text and "tokens" in incident.amount_text
        assert (incident.payload or {})["usage_tokens"] == 500
        assert (incident.payload or {})["hard_limit_tokens"] == 10
