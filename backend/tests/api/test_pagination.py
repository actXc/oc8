"""Two screens that grew without bound.

The department board returned EVERY task the department ever had, oldest first,
so a busy agent turned the page into an endless scroll whose top was the least
interesting end of it. The activity feed returned the newest 50 across ALL
agents, which the agent page then filtered client-side -- so an agent that had
been quiet for a while showed nothing at all, no matter how much history it had.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _h(tenant: uuid.UUID) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="op", role="org_admin")
    return {"Authorization": f"Bearer {token}"}


async def _get(tenant: uuid.UUID, path: str) -> Any:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get(f"/api/v1{path}", headers=_h(tenant))
            assert r.status_code == 200, r.text
            return r.json()


# ------------------------------------------------------------------- the board


async def _department(db: Any, tenant: uuid.UUID, *, done: int, open_: int) -> uuid.UUID:
    dept = m.Department(tenant_id=tenant, name="Kundenservice", frame={})
    db.add(dept)
    await db.flush()
    # Distinct created_at values, a minute apart. Rows written in one
    # transaction otherwise share the server clock, and uuid7 only breaks the tie
    # to millisecond precision -- so "newest" would be undefined here for reasons
    # that have nothing to do with what this test is about.
    base = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
    for i in range(done):
        db.add(
            m.Task(
                tenant_id=tenant,
                department_id=dept.id,
                title=f"erledigt {i}",
                state="done",
                created_at=base + dt.timedelta(minutes=i),
            )
        )
    for i in range(open_):
        db.add(
            m.Task(
                tenant_id=tenant,
                department_id=dept.id,
                title=f"offen {i}",
                state="in_progress",
                created_at=base + dt.timedelta(days=1, minutes=i),
            )
        )
    await db.flush()
    return dept.id


async def test_the_board_returns_the_newest_tasks_not_all_of_them(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept_id = await _department(db, tenant, done=40, open_=3)
        await db.commit()

    body = await _get(tenant, f"/departments/{dept_id}/board?limit=10")
    done = [t for t in body["tasks"] if t["column"] == "done"]
    assert len(done) == 10, "the limit applies PER COLUMN"
    # Newest first: a card from an hour ago is worth more than the first task
    # this department ever ran.
    assert done[0]["title"] == "erledigt 39"


async def test_a_busy_column_does_not_starve_a_quiet_one(
    app_session: AppSessionFactory,
) -> None:
    """A flat limit would fill up with `done` and hide the three tasks actually
    in progress -- the ones somebody opened the board to look at."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept_id = await _department(db, tenant, done=40, open_=3)
        await db.commit()

    body = await _get(tenant, f"/departments/{dept_id}/board?limit=5")
    columns = {t["column"] for t in body["tasks"]}
    assert "in_progress" in columns
    assert len([t for t in body["tasks"] if t["column"] == "in_progress"]) == 3


async def test_the_board_says_how_much_it_is_not_showing(
    app_session: AppSessionFactory,
) -> None:
    """Otherwise a truncated board is indistinguishable from a finished one."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept_id = await _department(db, tenant, done=40, open_=3)
        await db.commit()

    body = await _get(tenant, f"/departments/{dept_id}/board?limit=10")
    assert body["totals"]["done"] == 40
    assert body["totals"]["in_progress"] == 3


# ------------------------------------------------------------------- the feed


async def _events(db: Any, tenant: uuid.UUID, agent_id: uuid.UUID, n: int) -> None:
    for i in range(n):
        db.add(
            m.ActivityEvent(
                tenant_id=tenant, agent_id=agent_id, status="info", message=f"e{i}"
            )
        )
    await db.flush()


async def test_the_feed_can_be_asked_for_one_agent(
    app_session: AppSessionFactory,
) -> None:
    """Filtering client-side meant a quiet agent showed an empty feed while its
    own history sat just past the global limit."""
    tenant = uuid.uuid4()
    quiet, loud = uuid.uuid4(), uuid.uuid4()
    async with app_session(tenant) as db:
        await _events(db, tenant, loud, 60)
        await _events(db, tenant, quiet, 3)
        await db.commit()

    body = await _get(tenant, f"/activity?agentId={quiet}&limit=50")
    assert len(body) == 3
    assert {e["agentId"] for e in body} == {str(quiet)}


async def test_the_feed_pages_backwards_without_repeating_itself(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    async with app_session(tenant) as db:
        await _events(db, tenant, agent, 30)
        await db.commit()

    first = await _get(tenant, f"/activity?agentId={agent}&limit=10")
    assert len(first) == 10
    second = await _get(tenant, f"/activity?agentId={agent}&limit=10&before={first[-1]['id']}")
    assert len(second) == 10
    assert not {e["id"] for e in first} & {e["id"] for e in second}, "no page may repeat a row"
