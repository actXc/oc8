"""The clarification half of the scoped door, at the level the endpoint sits on.

Not named in §8, and it does not overlap `tests/api/test_workspace_clarifications.py`:
that file drives the two HTTP routes, which do not exist yet. This one drives
`oc8.workspace.queue` directly, because every way this module can be subtly wrong
is invisible through a route that returns 200 either way --

* answering with `may_view` instead of `may_decide` (a `dept_viewer` empties the
  queue he is only supposed to read),
* returning `None` for a foreign question AFTER having already written the answer,
* committing inside the request transaction, which unbinds `app.tenant_id` and
  makes everything the route does next silently see nothing,
* re-answering a closed question and thereby answering a NEWER one on the same
  run, since `resolve_clarification` works per-run and not per-row.

Every test mints its own tenant: `ACME_TENANT_ID` has no per-test rollback and
`org_member` carries `UNIQUE (tenant_id, subject)`.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.auth.principal import Principal
from oc8.authz.permissions import SEAT_APPROVER, SEAT_VIEWER
from oc8.authz.scope import HumanActor, scope_for_principal
from oc8.workspace.queue import NotAnswerable, answer_clarification, open_clarifications
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def _actor(db: Any, tenant: uuid.UUID, subject: str, role: str = "member") -> HumanActor:
    """Resolved the way the gate resolves it, from rows -- a `DepartmentScope`
    cannot be constructed, which is the point of it."""
    principal = Principal(subject=subject, tenant_id=tenant, role=role)
    member, scope = await scope_for_principal(db, principal, upsert=True)
    assert member is not None
    return HumanActor(principal=principal, member=member, scope=scope)


async def _seat(
    db: Any, tenant: uuid.UUID, subject: str, department_id: uuid.UUID, seat_role: str
) -> None:
    member: m.OrgMember | None = (
        await db.execute(
            select(m.OrgMember).where(
                m.OrgMember.subject == subject, m.OrgMember.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if member is None:
        member = m.OrgMember(
            tenant_id=tenant,
            subject=subject,
            subject_uuid=uuid.uuid5(uuid.NAMESPACE_URL, f"oc8:subject:{subject}"),
            display_name=subject,
        )
        db.add(member)
        await db.flush()
    db.add(
        m.OrgMemberDepartment(
            tenant_id=tenant,
            member_id=member.id,
            department_id=department_id,
            seat_role=seat_role,
        )
    )
    await db.flush()


async def _parked(
    db: Any,
    tenant: uuid.UUID,
    *,
    department_id: uuid.UUID,
    agent_name: str,
    question: str,
    state: str = "waiting_for_input",
) -> tuple[uuid.UUID, uuid.UUID]:
    """An agent that stopped to ask something. Returns (run_id, clarification_id)."""
    agent = m.Agent(tenant_id=tenant, department_id=department_id, name=agent_name)
    db.add(agent)
    await db.flush()
    run = m.AgentRun(
        tenant_id=tenant,
        agent_id=agent.id,
        state=state,
        context={"task": "t", "pending_question": question},
    )
    db.add(run)
    await db.flush()
    clar = m.Clarification(
        tenant_id=tenant, run_id=run.id, agent_id=agent.id, question=question, status="open"
    )
    db.add(clar)
    await db.flush()
    return run.id, clar.id


class _Office:
    tenant: uuid.UUID
    sales: uuid.UUID
    engineering: uuid.UUID
    sales_run: uuid.UUID
    sales_clar: uuid.UUID
    engineering_run: uuid.UUID
    engineering_clar: uuid.UUID


async def _office(app_session: AppSessionFactory) -> _Office:
    office = _Office()
    office.tenant = uuid.uuid4()
    async with app_session(office.tenant) as db:
        sales = m.Department(tenant_id=office.tenant, name="Vertrieb")
        engineering = m.Department(tenant_id=office.tenant, name="Entwicklung")
        db.add_all([sales, engineering])
        await db.flush()
        office.sales, office.engineering = sales.id, engineering.id
        office.sales_run, office.sales_clar = await _parked(
            db,
            office.tenant,
            department_id=office.sales,
            agent_name="Nora",
            question="Welche Rabattstufe für Gartenholz?",
        )
        office.engineering_run, office.engineering_clar = await _parked(
            db,
            office.tenant,
            department_id=office.engineering,
            agent_name="Ada",
            question="Darf ich die Migration nachts fahren?",
        )
    return office


# ---------------------------------------------------------------------- reading


async def test_the_queue_holds_only_this_seats_open_questions(
    app_session: AppSessionFactory,
) -> None:
    office = await _office(app_session)
    async with app_session(office.tenant) as db:
        await _seat(db, office.tenant, "hos", office.sales, SEAT_APPROVER)
        rows = await open_clarifications(db, actor=await _actor(db, office.tenant, "hos"))

    assert [r.id for r in rows] == [office.sales_clar]
    # The row is what the screen renders. A queue that can only print a uuid for
    # "who is asking" is not the row §7 describes, and resolving those two names
    # per row in the endpoint is how forty questions become eighty-one queries.
    assert rows[0].agent_name == "Nora"
    assert rows[0].department_name == "Vertrieb"
    assert rows[0].department_id == office.sales
    assert rows[0].run_id == office.sales_run
    assert rows[0].question == "Welche Rabattstufe für Gartenholz?"


async def test_an_answered_question_leaves_the_queue(app_session: AppSessionFactory) -> None:
    """Otherwise the screen offers the same question twice and the second answer
    lands on a run that is no longer waiting for one."""
    office = await _office(app_session)
    async with app_session(office.tenant) as db:
        await _seat(db, office.tenant, "hos", office.sales, SEAT_APPROVER)
        actor = await _actor(db, office.tenant, "hos")
        assert len(await open_clarifications(db, actor=actor)) == 1
        assert await answer_clarification(
            db, actor=actor, clarification_id=office.sales_clar, answer="Stufe 2"
        )
        assert await open_clarifications(db, actor=actor) == []


async def test_an_empty_scope_reads_nothing_rather_than_everything(
    app_session: AppSessionFactory,
) -> None:
    """The fail-open shape this module exists to make unwritable: `WHERE
    department_id IN ()` is not valid SQL, so "skip the filter when the set is
    empty" is the natural way to write it -- and it hands a seatless employee
    every parked question in the company."""
    office = await _office(app_session)
    async with app_session(office.tenant) as db:
        actor = await _actor(db, office.tenant, "nobody")
        assert actor.scope.is_empty is True
        assert await open_clarifications(db, actor=actor) == []


async def test_the_unrestricted_see_every_department(app_session: AppSessionFactory) -> None:
    """And by the boolean, not by a set of departments: `viewable` is EMPTY for
    an unrestricted caller, so a filter built from it shows a CEO nothing."""
    office = await _office(app_session)
    async with app_session(office.tenant) as db:
        actor = await _actor(db, office.tenant, "boss", role="org_admin")
        assert actor.scope.viewable == frozenset()
        rows = await open_clarifications(db, actor=actor)

    assert {r.id for r in rows} == {office.sales_clar, office.engineering_clar}


async def test_the_queue_is_bounded_and_newest_first(app_session: AppSessionFactory) -> None:
    """`limit` is the ceiling §5 puts on every list, and `max(1, ...)` is not
    decoration: a caller passing 0 or -1 must not get an empty page that reads
    like an empty queue, and Postgres refuses a negative LIMIT outright."""
    office = await _office(app_session)
    async with app_session(office.tenant) as db:
        actor = await _actor(db, office.tenant, "boss", role="org_admin")
        assert len(await open_clarifications(db, actor=actor, limit=1)) == 1
        assert len(await open_clarifications(db, actor=actor, limit=0)) == 1
        assert len(await open_clarifications(db, actor=actor, limit=-5)) == 1
        assert len(await open_clarifications(db, actor=actor, limit=10_000)) == 2

        newest = (await open_clarifications(db, actor=actor))[0]
        assert newest.id == office.engineering_clar, "newest first, as the approvals list is"


# --------------------------------------------------------------------- answering


async def test_answering_requeues_the_run_and_does_not_commit(
    app_session: AppSessionFactory,
) -> None:
    """Two properties in one test because they are two halves of the contract.

    The run comes BACK so the caller can commit and only then `publish_run` -- a
    stream entry whose run row is not yet visible is a run a worker picks up and
    cannot find. And nothing here commits: a commit inside a `tenant_session`
    unbinds `app.tenant_id`, and every statement after it in the route body
    silently sees no rows at all rather than failing.
    """
    office = await _office(app_session)
    async with app_session(office.tenant) as db:
        await _seat(db, office.tenant, "hos", office.sales, SEAT_APPROVER)
        actor = await _actor(db, office.tenant, "hos")
        run = await answer_clarification(
            db,
            actor=actor,
            clarification_id=office.sales_clar,
            answer="Stufe 2, wie im letzten Quartal",
        )
        assert run is not None and run.id == office.sales_run
        assert run.state == "queued"

        # The tenant binding is still alive on THIS session. If the module had
        # committed, this read would come back empty and nothing would raise.
        still_here = (
            await db.execute(select(m.Department).where(m.Department.id == office.sales))
        ).scalar_one_or_none()
        assert still_here is not None, "answer_clarification committed and unbound app.tenant_id"

    async with app_session(office.tenant) as db:
        clar = await db.get(m.Clarification, office.sales_clar)
        assert clar is not None
        assert clar.status == "answered"
        assert clar.answer == "Stufe 2, wie im letzten Quartal"
        parked = await db.get(m.AgentRun, office.sales_run)
        assert parked is not None and parked.state == "queued"
        # Where the executor will actually look for it.
        assert {"question": clar.question, "answer": clar.answer} in parked.context[
            "clarifications"
        ]
        assert "pending_question" not in parked.context


async def test_another_departments_question_is_refused_and_nothing_is_written(
    app_session: AppSessionFactory,
) -> None:
    """`None` for "not yours" AND for "no such row" -- but the assertion that
    matters is the second half: a funnel that answered the question and then
    reported None would satisfy a return-value-only test while the agent had
    already been told to carry on."""
    office = await _office(app_session)
    async with app_session(office.tenant) as db:
        await _seat(db, office.tenant, "hos", office.sales, SEAT_APPROVER)
        actor = await _actor(db, office.tenant, "hos")
        assert (
            await answer_clarification(
                db, actor=actor, clarification_id=office.engineering_clar, answer="ja"
            )
            is None
        )
        assert (
            await answer_clarification(db, actor=actor, clarification_id=uuid.uuid4(), answer="ja")
            is None
        )

    async with app_session(office.tenant) as db:
        foreign = await db.get(m.Clarification, office.engineering_clar)
        assert foreign is not None
        assert foreign.status == "open" and foreign.answer is None
        parked = await db.get(m.AgentRun, office.engineering_run)
        assert parked is not None and parked.state == "waiting_for_input"


async def test_a_viewer_seat_reads_the_queue_and_cannot_empty_it(
    app_session: AppSessionFactory,
) -> None:
    """The test that catches `may_view` where `may_decide` belongs.

    `clarification:answer` is the decide-level half of `SEAT_PERMISSIONS`, so a
    `dept_viewer` sees the question and cannot answer it. Written with the same
    row for both calls, so a gate that used the view predicate would pass every
    other test in this file.
    """
    office = await _office(app_session)
    async with app_session(office.tenant) as db:
        await _seat(db, office.tenant, "watcher", office.sales, SEAT_VIEWER)
        actor = await _actor(db, office.tenant, "watcher")
        assert [r.id for r in await open_clarifications(db, actor=actor)] == [office.sales_clar]
        assert (
            await answer_clarification(
                db, actor=actor, clarification_id=office.sales_clar, answer="Stufe 2"
            )
            is None
        )

    async with app_session(office.tenant) as db:
        clar = await db.get(m.Clarification, office.sales_clar)
        assert clar is not None and clar.status == "open"


async def test_a_run_that_is_no_longer_waiting_says_so(app_session: AppSessionFactory) -> None:
    """409, not 200: recording an answer for a run that failed or was cancelled
    while the question sat in the queue tells the person their sentence reached
    an agent that will never read it."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb")
        db.add(dept)
        await db.flush()
        _run_id, clar_id = await _parked(
            db, tenant, department_id=dept.id, agent_name="Nora", question="?", state="failed"
        )
        await _seat(db, tenant, "hos", dept.id, SEAT_APPROVER)
        actor = await _actor(db, tenant, "hos")
        with pytest.raises(NotAnswerable):
            await answer_clarification(db, actor=actor, clarification_id=clar_id, answer="ja")


async def test_a_question_with_no_readable_agent_is_listed_by_nobody_and_answerable_by_nobody(
    app_session: AppSessionFactory,
) -> None:
    """`clarification.agent_id` carries no foreign key, and the department is
    read through it. A row whose agent is gone therefore has no department --
    which `may_decide` would read as NULL, i.e. TENANT-WIDE, i.e. answerable by
    anybody unrestricted, while `open_clarifications`' inner join leaves it out
    of every queue. The two doors must agree about which rows exist."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Vertrieb")
        db.add(dept)
        await db.flush()
        run = m.AgentRun(
            tenant_id=tenant,
            agent_id=uuid.uuid4(),  # no such agent, and nothing enforces one
            state="waiting_for_input",
            context={"task": "t"},
        )
        db.add(run)
        await db.flush()
        orphan = m.Clarification(
            tenant_id=tenant,
            run_id=run.id,
            agent_id=run.agent_id,
            question="?",
            status="open",
        )
        db.add(orphan)
        await db.flush()

        boss = await _actor(db, tenant, "boss", role="org_admin")
        assert boss.scope.is_unrestricted is True
        assert await open_clarifications(db, actor=boss) == []
        assert (
            await answer_clarification(db, actor=boss, clarification_id=orphan.id, answer="ja")
            is None
        )


async def test_re_answering_a_closed_question_cannot_answer_a_newer_one(
    app_session: AppSessionFactory,
) -> None:
    """The reason the status check is not redundant with the run-state check.

    `resolve_clarification` answers every OPEN clarification on the RUN, so a run
    that has since parked on a second question would have that second question
    answered by somebody re-submitting the first -- with the first answer, out of
    a stale browser tab, and the run would resume as if a person had replied.
    """
    office = await _office(app_session)
    async with app_session(office.tenant) as db:
        await _seat(db, office.tenant, "hos", office.sales, SEAT_APPROVER)
        actor = await _actor(db, office.tenant, "hos")
        await answer_clarification(
            db, actor=actor, clarification_id=office.sales_clar, answer="Stufe 2"
        )

        # The agent picked the run back up and asked something else.
        run = await db.get(m.AgentRun, office.sales_run)
        assert run is not None
        run.state = "waiting_for_input"
        second = m.Clarification(
            tenant_id=office.tenant,
            run_id=office.sales_run,
            agent_id=run.agent_id,
            question="Und der Liefertermin?",
            status="open",
        )
        db.add(second)
        await db.flush()

        with pytest.raises(NotAnswerable):
            await answer_clarification(
                db, actor=actor, clarification_id=office.sales_clar, answer="Stufe 2"
            )
        assert second.status == "open", "the older answer was folded onto the newer question"
