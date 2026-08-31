"""`GET /agents/{id}/kpis`, `GET /departments/{id}/kpis`, `GET /kpis` --
Task 4 of the agent-KPIs plan. The numbers themselves are `compute_kpis`'s
concern (backend/tests/kpis/test_aggregate.py); this file is about who may
reach them and how the three routes are scoped.

The agent/department routes are `require_departmental` -- READ graduates for
any live seat -- narrowed by `visible_agent`/`visible_department`, exactly
like `GET /agents/{id}`/`GET /departments/{id}`: the door only proves the
caller holds the resource's `:view` permission SOMEWHERE, the row itself
still has to be proven visible. `GET /kpis` is tenant-wide-only, gated on the
new `statistics:view` permission.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.api.v1.kpis import MAX_GROUPS
from oc8.auth import get_identity_provider
from oc8.authz.permissions import SEAT_VIEWER
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

T0 = dt.datetime(2026, 8, 1, 12, 0, 0, tzinfo=dt.UTC)


def _headers(tenant: uuid.UUID, subject: str, role: str = "member") -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=role)
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


def _subject_uuid(subject: str) -> uuid.UUID:
    try:
        return uuid.UUID(subject)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"oc8:subject:{subject}")


def _run(
    tenant: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    created_at: dt.datetime = T0,
    state: str = "done",
) -> m.AgentRun:
    return m.AgentRun(
        id=uuid.uuid4(),
        tenant_id=tenant,
        agent_id=agent_id,
        state=state,
        context={},
        messages=[],
        created_at=created_at,
        updated_at=created_at,
    )


@dataclass
class _Office:
    tenant: uuid.UUID
    sales: uuid.UUID
    engineering: uuid.UUID
    sales_agent: uuid.UUID
    engineering_agent: uuid.UUID
    #: A live `dept_viewer` seat in `sales` ONLY. No tenant-wide grant.
    seated_subject: str = "sales-seat"


async def _office(
    app_session: AppSessionFactory, *, sales_runs: int = 2, engineering_runs: int = 1
) -> _Office:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
        engineering = m.Department(tenant_id=tenant, name="Entwicklung", frame={})
        db.add_all([sales, engineering])
        await db.flush()

        sales_agent = m.Agent(
            tenant_id=tenant,
            department_id=sales.id,
            name="Nora",
            status="stopped",
            narrowing={},
            definition={},
            presentation={},
        )
        engineering_agent = m.Agent(
            tenant_id=tenant,
            department_id=engineering.id,
            name="Theo",
            status="stopped",
            narrowing={},
            definition={},
            presentation={},
        )
        db.add_all([sales_agent, engineering_agent])
        await db.flush()

        db.add_all([_run(tenant, sales_agent.id) for _ in range(sales_runs)])
        db.add_all([_run(tenant, engineering_agent.id) for _ in range(engineering_runs)])
        await db.flush()

        office = _Office(
            tenant=tenant,
            sales=sales.id,
            engineering=engineering.id,
            sales_agent=sales_agent.id,
            engineering_agent=engineering_agent.id,
        )

        member = m.OrgMember(
            tenant_id=tenant,
            subject=office.seated_subject,
            subject_uuid=_subject_uuid(office.seated_subject),
        )
        db.add(member)
        await db.flush()
        db.add(
            m.OrgMemberDepartment(
                tenant_id=tenant,
                member_id=member.id,
                department_id=sales.id,
                seat_role=SEAT_VIEWER,
            )
        )
        await db.flush()
    return office


# --------------------------------------------------------------- agent scope


async def test_agent_kpis_scoped_to_its_own_agent(app_session: AppSessionFactory) -> None:
    office = await _office(app_session, sales_runs=2, engineering_runs=1)
    async with _http() as http:
        h = _headers(office.tenant, office.seated_subject)
        resp = await http.get(f"/api/v1/agents/{office.sales_agent}/kpis", headers=h)
        assert resp.status_code == 200, resp.text
        assert resp.json()["runCount"] == 2


async def test_agent_kpis_404s_on_a_foreign_agent(app_session: AppSessionFactory) -> None:
    """A Sales-seated caller has `agent:view` (via her seat), so the door
    admits her -- but the row itself must still 404 the same way
    `GET /agents/{id}` already does for Engineering's agent, or the KPI route
    would be a side channel around the departmental read scope."""
    office = await _office(app_session)
    async with _http() as http:
        h = _headers(office.tenant, office.seated_subject)
        resp = await http.get(f"/api/v1/agents/{office.engineering_agent}/kpis", headers=h)
        assert resp.status_code == 404, resp.text
        assert "agent not found" in resp.text


async def test_agent_kpis_requires_agent_view_permission(app_session: AppSessionFactory) -> None:
    """A caller with no seat anywhere and the empty-set `member` role holds
    `agent:view` nowhere, so `require_departmental` refuses before any row is
    even looked up."""
    office = await _office(app_session)
    async with _http() as http:
        h = _headers(office.tenant, "no-seat-at-all")
        resp = await http.get(f"/api/v1/agents/{office.sales_agent}/kpis", headers=h)
        assert resp.status_code == 403, resp.text


# ---------------------------------------------------------- department scope


async def test_department_kpis_scoped_to_its_agents(app_session: AppSessionFactory) -> None:
    office = await _office(app_session, sales_runs=2, engineering_runs=1)
    async with _http() as http:
        h = _headers(office.tenant, office.seated_subject)
        resp = await http.get(f"/api/v1/departments/{office.sales}/kpis", headers=h)
        assert resp.status_code == 200, resp.text
        assert resp.json()["runCount"] == 2

        foreign = await http.get(f"/api/v1/departments/{office.engineering}/kpis", headers=h)
        assert foreign.status_code == 404, foreign.text


# --------------------------------------------------------------- tenant-wide


async def test_tenant_kpis_sees_everything(app_session: AppSessionFactory) -> None:
    office = await _office(app_session, sales_runs=2, engineering_runs=1)
    async with _http() as http:
        h = _headers(office.tenant, "the-admin", role="org_admin")
        resp = await http.get("/api/v1/kpis", headers=h)
        assert resp.status_code == 200, resp.text
        assert resp.json()["runCount"] == 3


async def test_tenant_kpis_requires_statistics_view_permission(
    app_session: AppSessionFactory,
) -> None:
    """A `member`-role principal holds the empty tenant-wide set, and
    `statistics:view` has no seat-scoped path (`GET /kpis` is gated by
    `require_permission`, not `require_departmental`) -- so no seat, however
    many she holds, can ever admit her here."""
    office = await _office(app_session)
    async with _http() as http:
        h = _headers(office.tenant, office.seated_subject)
        resp = await http.get("/api/v1/kpis", headers=h)
        assert resp.status_code == 403, resp.text


async def test_tenant_kpis_rejects_an_unknown_group_by(app_session: AppSessionFactory) -> None:
    office = await _office(app_session)
    async with _http() as http:
        h = _headers(office.tenant, "the-admin", role="org_admin")
        resp = await http.get("/api/v1/kpis", params={"groupBy": "year"}, headers=h)
        assert resp.status_code == 422, resp.text


async def test_tenant_kpis_group_by_agent_returns_one_row_per_agent(
    app_session: AppSessionFactory,
) -> None:
    office = await _office(app_session, sales_runs=2, engineering_runs=1)
    async with _http() as http:
        h = _headers(office.tenant, "the-admin", role="org_admin")
        resp = await http.get("/api/v1/kpis", params={"groupBy": "agent"}, headers=h)
        assert resp.status_code == 200, resp.text
        rows = {row["groupKey"]: row["runCount"] for row in resp.json()["rows"]}
        assert rows == {
            str(office.sales_agent): 2,
            str(office.engineering_agent): 1,
        }


async def test_tenant_kpis_group_by_department_returns_one_row_per_department(
    app_session: AppSessionFactory,
) -> None:
    office = await _office(app_session, sales_runs=2, engineering_runs=1)
    async with _http() as http:
        h = _headers(office.tenant, "the-admin", role="org_admin")
        resp = await http.get("/api/v1/kpis", params={"groupBy": "department"}, headers=h)
        assert resp.status_code == 200, resp.text
        rows = {row["groupKey"]: row["runCount"] for row in resp.json()["rows"]}
        assert rows == {str(office.sales): 2, str(office.engineering): 1}


async def test_tenant_kpis_group_by_day_buckets_correctly(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant,
            department_id=dept.id,
            name="Nora",
            status="stopped",
            narrowing={},
            definition={},
            presentation={},
        )
        db.add(agent)
        await db.flush()
        db.add_all(
            [
                _run(tenant, agent.id, created_at=T0),
                _run(tenant, agent.id, created_at=T0 + dt.timedelta(days=2)),
            ]
        )
        await db.flush()

    async with _http() as http:
        h = _headers(tenant, "the-admin", role="org_admin")
        resp = await http.get(
            "/api/v1/kpis",
            params={
                "groupBy": "day",
                "dateFrom": (T0 - dt.timedelta(hours=1)).isoformat(),
                "dateTo": (T0 + dt.timedelta(days=3)).isoformat(),
            },
            headers=h,
        )
        assert resp.status_code == 200, resp.text
        rows = {row["groupKey"]: row["runCount"] for row in resp.json()["rows"]}
        assert rows.get(T0.date().isoformat()) == 1
        assert rows.get((T0 + dt.timedelta(days=2)).date().isoformat()) == 1
        assert sum(rows.values()) == 2


async def _bare_tenant_with_agent(app_session: AppSessionFactory) -> tuple[uuid.UUID, uuid.UUID]:
    """A tenant + department + single agent, no seats -- for the tenant-wide
    tests below that only need `org_admin` and don't care about departmental
    scoping."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant,
            department_id=dept.id,
            name="Nora",
            status="stopped",
            narrowing={},
            definition={},
            presentation={},
        )
        db.add(agent)
        await db.flush()
        agent_id = agent.id
    return tenant, agent_id


async def test_tenant_kpis_handles_a_naive_date_from_without_500(
    app_session: AppSessionFactory,
) -> None:
    """`dateFrom=2026-08-01T00:00:00` (no offset) used to reach the bucket
    loop as a naive `datetime` while `range_end` defaulted to an
    offset-aware `dt.datetime.now(dt.UTC)`, raising `TypeError: can't compare
    offset-naive and offset-aware datetimes` at `while cursor < range_end`.
    A naive bound must be anchored to UTC instead, the same fix `audit.py`'s
    `_parse_bound` already applies to `GET /audit`."""
    tenant, agent_id = await _bare_tenant_with_agent(app_session)
    async with app_session(tenant) as db:
        db.add(_run(tenant, agent_id, created_at=T0))
        await db.flush()

    async with _http() as http:
        h = _headers(tenant, "the-admin", role="org_admin")
        resp = await http.get(
            "/api/v1/kpis",
            params={"groupBy": "day", "dateFrom": "2026-08-01T00:00:00"},
            headers=h,
        )
        assert resp.status_code == 200, resp.text

        # The naive bound must also reach the UNGROUPED endpoints and the two
        # scoped ones the same way -- not just the bucketing path.
        ungrouped = await http.get(
            "/api/v1/kpis", params={"dateFrom": "2026-08-01T00:00:00"}, headers=h
        )
        assert ungrouped.status_code == 200, ungrouped.text
        assert ungrouped.json()["runCount"] == 1


async def test_tenant_kpis_group_by_day_rejects_an_unbounded_range(
    app_session: AppSessionFactory,
) -> None:
    """`dateFrom=1900-01-01&dateTo=2026-01-01&groupBy=day` would otherwise
    produce over 46,000 buckets, each firing `compute_kpis`'s six sub-queries
    -- one HTTP request turned into hundreds of thousands of DB round trips.
    Rejected with a 422 naming the limit, not silently truncated."""
    tenant, _agent_id = await _bare_tenant_with_agent(app_session)
    async with _http() as http:
        h = _headers(tenant, "the-admin", role="org_admin")
        resp = await http.get(
            "/api/v1/kpis",
            params={"groupBy": "day", "dateFrom": "1900-01-01", "dateTo": "2026-01-01"},
            headers=h,
        )
        assert resp.status_code == 422, resp.text


async def test_tenant_kpis_group_by_day_floors_bucket_boundaries_to_midnight(
    app_session: AppSessionFactory,
) -> None:
    """`dateFrom` carries a non-midnight time-of-day (14:32). Without
    flooring, the first bucket would be `[Aug1 14:32, Aug2 14:32)`, and a run
    at `Aug2 09:00` -- which a human calling this "August 2nd" -- would fall
    into that bucket and be labeled `2026-08-01`. Flooring `range_start` to
    midnight UTC fixes the alignment for every subsequent bucket too."""
    tenant, agent_id = await _bare_tenant_with_agent(app_session)
    misaligned_run_at = dt.datetime(2026, 8, 2, 9, 0, 0, tzinfo=dt.UTC)
    async with app_session(tenant) as db:
        db.add(_run(tenant, agent_id, created_at=misaligned_run_at))
        await db.flush()

    async with _http() as http:
        h = _headers(tenant, "the-admin", role="org_admin")
        resp = await http.get(
            "/api/v1/kpis",
            params={
                "groupBy": "day",
                "dateFrom": "2026-08-01T14:32:00Z",
                "dateTo": "2026-08-03T00:00:00Z",
            },
            headers=h,
        )
        assert resp.status_code == 200, resp.text
        rows = {row["groupKey"]: row["runCount"] for row in resp.json()["rows"]}
        assert rows.get("2026-08-02") == 1, rows
        assert rows.get("2026-08-01", 0) == 0, rows


async def test_tenant_kpis_group_by_month_floors_to_the_first_of_the_month(
    app_session: AppSessionFactory,
) -> None:
    tenant, agent_id = await _bare_tenant_with_agent(app_session)
    async with app_session(tenant) as db:
        db.add(_run(tenant, agent_id, created_at=dt.datetime(2026, 8, 15, 9, 0, tzinfo=dt.UTC)))
        await db.flush()

    async with _http() as http:
        h = _headers(tenant, "the-admin", role="org_admin")
        resp = await http.get(
            "/api/v1/kpis",
            params={
                "groupBy": "month",
                "dateFrom": "2026-08-15T00:00:00Z",
                "dateTo": "2026-09-15T00:00:00Z",
            },
            headers=h,
        )
        assert resp.status_code == 200, resp.text
        labels = {row["groupKey"] for row in resp.json()["rows"]}
        # The first bucket must start on the 1st, not the 15th.
        assert "2026-08-01" in labels, labels


async def test_tenant_kpis_status_filter_narrows_to_one_run_state(
    app_session: AppSessionFactory,
) -> None:
    tenant, agent_id = await _bare_tenant_with_agent(app_session)
    async with app_session(tenant) as db:
        db.add_all(
            [
                _run(tenant, agent_id, state="done"),
                _run(tenant, agent_id, state="failed"),
            ]
        )
        await db.flush()

    async with _http() as http:
        h = _headers(tenant, "the-admin", role="org_admin")
        done_only = await http.get("/api/v1/kpis", params={"status": "done"}, headers=h)
        assert done_only.status_code == 200, done_only.text
        assert done_only.json()["runCount"] == 1

        unfiltered = await http.get("/api/v1/kpis", headers=h)
        assert unfiltered.status_code == 200, unfiltered.text
        assert unfiltered.json()["runCount"] == 2


async def test_tenant_kpis_group_by_agent_rejects_more_groups_than_the_cap(
    app_session: AppSessionFactory,
) -> None:
    """`groupBy=agent`/`department` had NO cap at all: unlike the bucket case,
    the group count comes from rows rather than a date range, so a tenant with
    thousands of agents turned one authenticated request into thousands of
    `compute_kpis` calls -- six sub-queries each. Rejected with a 422 naming
    the limit BEFORE any per-group query runs, exactly like the bucket cap."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
        db.add(dept)
        await db.flush()
        db.add_all(
            [
                m.Agent(
                    tenant_id=tenant,
                    department_id=dept.id,
                    name=f"Agent {i}",
                    status="stopped",
                    narrowing={},
                    definition={},
                    presentation={},
                )
                for i in range(MAX_GROUPS + 1)
            ]
        )
        await db.flush()

    async with _http() as http:
        h = _headers(tenant, "the-admin", role="org_admin")
        resp = await http.get("/api/v1/kpis", params={"groupBy": "agent"}, headers=h)
        assert resp.status_code == 422, resp.text
        assert str(MAX_GROUPS) in resp.json()["detail"]

        # ...while the same 101 agents grouped by DEPARTMENT are one group, so
        # the cap rejects only what is actually oversized.
        narrowed = await http.get("/api/v1/kpis", params={"groupBy": "department"}, headers=h)
        assert narrowed.status_code == 200, narrowed.text


async def test_tenant_kpis_group_by_department_includes_a_department_with_no_agents(
    app_session: AppSessionFactory,
) -> None:
    """The department list used to be derived from `Agent.department_id`, so a
    department with no live agent produced no row at all -- not even a zero one
    -- even when it held decided approvals, which carry `department_id` on
    themselves and need no agent to exist (spec §1.3). Absence and "nothing to
    report" are different statements, and only one of them is true here."""
    office = await _office(app_session, sales_runs=2, engineering_runs=1)
    async with app_session(office.tenant) as db:
        empty = m.Department(tenant_id=office.tenant, name="Recht", frame={})
        db.add(empty)
        await db.flush()
        empty_id = empty.id
        db.add(
            m.ApprovalRequest(
                tenant_id=office.tenant,
                # The approval's own denormalised department, not the agent's:
                # this is exactly the row that survives an agent moving away.
                agent_id=office.sales_agent,
                department_id=empty_id,
                action_type="tool_send",
                status="approved",
                created_at=T0,
                decided_at=T0 + dt.timedelta(seconds=30),
            )
        )
        await db.flush()

    async with _http() as http:
        h = _headers(office.tenant, "the-admin", role="org_admin")
        resp = await http.get("/api/v1/kpis", params={"groupBy": "department"}, headers=h)
        assert resp.status_code == 200, resp.text
        rows = {row["groupKey"]: row for row in resp.json()["rows"]}

        assert str(empty_id) in rows, "a department with no live agent must still get a row"
        row = rows[str(empty_id)]
        assert row["approvalWaitMs"] == 30_000
        assert row["runCount"] == 0
        assert row["totalDurationMs"] is None
        assert row["executionDurationMs"] is None
        assert row["responseTimeMs"] is None
        assert row["avgToolCallDurationMs"] is None

        # The two departments that DO have agents are unchanged.
        assert rows[str(office.sales)]["runCount"] == 2
        assert rows[str(office.engineering)]["runCount"] == 1


async def test_dept_manager_does_not_get_statistics_view_by_default(
    app_session: AppSessionFactory,
) -> None:
    """`statistics:view` is excluded from `_VIEW_EVERYTHING` (same mechanism
    `audit:view` already uses) precisely so a `dept_manager` seated in one
    department cannot read the WHOLE tenant's numbers through `GET /kpis` by
    virtue of the role's default grants alone -- the seat mechanism has no
    way to narrow a `require_permission`-gated route."""
    tenant, _agent_id = await _bare_tenant_with_agent(app_session)
    async with _http() as http:
        h = _headers(tenant, "a-dept-manager", role="dept_manager")
        resp = await http.get("/api/v1/kpis", headers=h)
        assert resp.status_code == 403, resp.text
