"""`assign_skill`'s re-enable-existing branch calls `db.commit()` instead of
`db.flush()` (`agents_write.py`). Pre-existing, bundled into this design's
commit since the file is being touched anyway (decision: the population
reachable through this route is about to grow from "org_admin only" to every
toggled department, so the drive-by fix rides with it).

A commit inside `tenant_session` unbinds `app.tenant_id`: the `SET LOCAL` that
binds it lives for exactly one transaction, and `COMMIT` ends that transaction.
Every statement issued afterward on the SAME session runs unbound, and RLS does
not raise for that -- it silently returns zero rows, which is why this class of
bug is invisible through a route that only ever returns one flat status string
and was never proven to see its own writes again.

Modelled on `tests/workspace/test_queue.py::
test_answering_requeues_the_run_and_does_not_commit`: drive the route's own
async function directly, on an `app_session`-bound session, so a statement
issued on the SAME session immediately afterward is the assertion -- not a
second HTTP request, which would open its own connection and bind its own GUC,
proving nothing about whether THIS request's session survived its own write.

`assign_skill` now also takes `request`/`actor` (`require_agent_write`'s
`HumanActor`, `api/deps.py`) rather than a bare `principal` -- the department-
scoped write gate this same design adds. `role="org_admin"` takes the
tenant-wide bypass inside `authorize_agent_write`, which reads `actor.
principal` alone and never touches `actor.scope`, so a minimal, directly-
resolved actor (not run through the HTTP layer) is enough to reach the branch
this test is actually about.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from starlette.requests import Request

from oc8 import models as m
from oc8.api.v1.agents_write import assign_skill
from oc8.auth.principal import Principal
from oc8.authz.scope import HumanActor, scope_for_principal
from oc8.schemas.requests import AssignSkillRequest
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _bare_request() -> Request:
    """A `Request` with nowhere it came from -- enough for `authority_for_
    principal`'s `request.state` memoisation, which is all `authorize_agent_
    write` asks of it. No ASGI app runs behind this test, so there is no real
    request to reuse."""
    return Request(scope={"type": "http", "headers": []})


async def test_reenabling_an_existing_skill_assignment_does_not_commit(
    app_session: AppSessionFactory,
) -> None:
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

        skill = m.Skill(tenant_id=tenant, name="S", origin="local", trust_level="first_party")
        db.add(skill)
        await db.flush()
        version = m.SkillVersion(
            tenant_id=tenant,
            skill_id=skill.id,
            semver="1.0.0",
            definition={"requires": {}},
            artifact_hash=uuid.uuid4().bytes,
        )
        db.add(version)
        await db.flush()
        skill.current_version_id = version.id
        await db.flush()

        # Already assigned, but disabled -- exactly the branch that commits.
        assignment = m.SkillAssignment(
            tenant_id=tenant, agent_id=agent.id, skill_version_id=version.id, enabled=False
        )
        db.add(assignment)
        await db.flush()
        assignment_id = assignment.id

        principal = Principal(subject="op", tenant_id=tenant, role="org_admin")
        member, scope = await scope_for_principal(db, principal, upsert=True)
        assert member is not None
        actor = HumanActor(principal=principal, member=member, scope=scope)
        body = AssignSkillRequest(skill_version_id=version.id)

        result = await assign_skill(agent.id, body, db, _bare_request(), actor)
        assert result == {"status": "already_assigned"}

        # THE assertion. If `assign_skill` had committed, `app.tenant_id` is gone
        # for the rest of this session and this SELECT -- issued on the exact
        # same `db`, with no intervening commit of our own -- comes back empty
        # rather than raising, which is what makes the bug silent in production.
        still_here = (
            await db.execute(select(m.Department).where(m.Department.id == dept.id))
        ).scalar_one_or_none()
        assert still_here is not None, (
            "assign_skill's re-enable branch committed and unbound app.tenant_id -- "
            "a statement issued later in the SAME request now silently sees nothing"
        )

    async with app_session(tenant) as db:
        row = await db.get(m.SkillAssignment, assignment_id)
        assert row is not None and row.enabled is True
