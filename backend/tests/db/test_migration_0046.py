"""0046: the department an approval is filed under, and the shape of a seat.

The backfill is exercised as the literal statement out of the migration module,
run on a connection that is NOT subject to RLS -- because that is the migration's
own situation, and the whole point of the `ag.tenant_id = a.tenant_id` term is
something an RLS-scoped session could never observe. The transaction is rolled
back, so the shared test database is unchanged.

The rest is asserted against the migrated schema itself rather than against the
ORM's opinion of it: a CHECK that only lives in `models/identity.py` is a comment,
and the claim being made here is that `dept_manager` is unrepresentable in the
TABLE.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from oc8 import models as m
from oc8.authz.permissions import SEAT_APPROVER, SEAT_PERMISSIONS, SEAT_VIEWER
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

_PATH = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0046_org_member_seats.py"


def _module() -> object:
    spec = importlib.util.spec_from_file_location("oc8_migration_0046", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _agent(conn: sa.Connection, tenant: uuid.UUID, department: uuid.UUID) -> uuid.UUID:
    agent_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO agent (id, tenant_id, department_id, name, role_title, mission, "
            " definition, narrowing, narrowing_overridden_keys, is_team_lead, status, "
            " trust_level, presentation, created_at, updated_at) "
            "VALUES (:id, :t, :d, 'A', '', '', '{}', '{}', '[]', false, 'idle', 'first_party', "
            " '{}', now(), now())"
        ),
        {"id": agent_id, "t": str(tenant), "d": str(department)},
    )
    return agent_id


def _approval(
    conn: sa.Connection,
    tenant: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    department_id: uuid.UUID | None = None,
) -> uuid.UUID:
    approval_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO approval_request (id, tenant_id, agent_id, department_id, action_type, "
            " payload, status, title, detail, created_at, updated_at) "
            "VALUES (:id, :t, :a, :d, 'tool_send', '{}', 'pending', '', '', now(), now())"
        ),
        {
            "id": approval_id,
            "t": str(tenant),
            "a": str(agent_id),
            "d": str(department_id) if department_id else None,
        },
    )
    return approval_id


async def test_the_backfill_files_an_approval_under_its_agents_department(pg_url: str) -> None:
    """Three rows, three different reasons, one statement.

    The cross-tenant row is the one worth the extra predicate: `agent_id` carries
    no foreign key, so a corrupted or migrated-in value pointing at another
    tenant's agent would, without `ag.tenant_id = a.tenant_id`, file the approval
    under a FOREIGN tenant's department id -- and a department id that does not
    exist in this tenant matches no seat, so the row would vanish from every
    human's queue while still holding a run open. Left NULL it means tenant-wide,
    which the unrestricted still see.

    The already-attributed row guards the `IS NULL` term: an agent that has been
    MOVED must not have its pending approvals re-filed into the department its
    approvers were never asked about.
    """
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    sales, engineering, elsewhere = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    engine = sa.create_engine(pg_url)
    with engine.connect() as conn:
        ours = _agent(conn, tenant_a, sales)
        theirs = _agent(conn, tenant_b, engineering)

        derived = _approval(conn, tenant_a, ours)
        foreign = _approval(conn, tenant_a, theirs)
        pinned = _approval(conn, tenant_a, ours, department_id=elsewhere)

        conn.execute(sa.text(str(_module()._BACKFILL)))  # type: ignore[attr-defined]

        rows: dict[uuid.UUID, uuid.UUID | None] = {
            row.id: row.department_id
            for row in conn.execute(
                sa.text("SELECT id, department_id FROM approval_request WHERE id = ANY(:ids)"),
                {"ids": [derived, foreign, pinned]},
            ).all()
        }
        conn.rollback()
    engine.dispose()

    assert rows[derived] == sales
    assert rows[foreign] is None, (
        "an agent_id pointing into another tenant must not import that tenant's department id"
    )
    assert rows[pinned] == elsewhere, "a department already pinned at raise time is never rewritten"


@pytest.mark.parametrize("seat_role", sorted(SEAT_PERMISSIONS))
async def test_the_table_admits_exactly_the_seat_roles_the_vocabulary_defines(
    app_session: AppSessionFactory, seat_role: str
) -> None:
    """Driven off `SEAT_PERMISSIONS` so that adding a seat role in code without
    adding it to the CHECK fails here rather than at the first grant."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(
            m.OrgMemberDepartment(
                tenant_id=tenant,
                member_id=uuid.uuid4(),
                department_id=uuid.uuid4(),
                seat_role=seat_role,
            )
        )
        await db.flush()


async def test_a_seat_cannot_name_a_built_in_role(app_session: AppSessionFactory) -> None:
    """`dept_manager` is nine tenant-wide `:manage` grants. The CHECK is what
    makes "a seat never carries a built-in role" a property of the table rather
    than of whichever code path happens to write the row."""
    tenant = uuid.uuid4()
    with pytest.raises(IntegrityError, match="ck_org_member_department_seat_role"):
        async with app_session(tenant) as db:
            db.add(
                m.OrgMemberDepartment(
                    tenant_id=tenant,
                    member_id=uuid.uuid4(),
                    department_id=uuid.uuid4(),
                    seat_role="dept_manager",
                )
            )
            await db.flush()


async def test_a_revoked_seat_leaves_room_for_a_new_one_and_a_live_one_does_not(
    app_session: AppSessionFactory,
) -> None:
    """The partial unique index is what lets revocation be an UPDATE instead of a
    DELETE -- "who could approve this, and until when" survives a re-grant."""
    tenant = uuid.uuid4()
    member, department = uuid.uuid4(), uuid.uuid4()
    async with app_session(tenant) as db:
        first = m.OrgMemberDepartment(
            tenant_id=tenant, member_id=member, department_id=department, seat_role=SEAT_VIEWER
        )
        db.add(first)
        await db.flush()
        first.revoked_at = sa.func.now()
        await db.flush()
        db.add(
            m.OrgMemberDepartment(
                tenant_id=tenant,
                member_id=member,
                department_id=department,
                seat_role=SEAT_APPROVER,
            )
        )
        await db.flush()
        live = (
            await db.execute(
                sa.select(sa.func.count())
                .select_from(m.OrgMemberDepartment)
                .where(
                    m.OrgMemberDepartment.member_id == member,
                    m.OrgMemberDepartment.revoked_at.is_(None),
                )
            )
        ).scalar_one()
        assert live == 1

    with pytest.raises(IntegrityError, match="uq_org_member_department_live"):
        async with app_session(tenant) as db:
            db.add(
                m.OrgMemberDepartment(
                    tenant_id=tenant,
                    member_id=member,
                    department_id=department,
                    seat_role=SEAT_VIEWER,
                )
            )
            await db.flush()


async def test_two_people_may_share_a_subject_only_across_tenants(
    app_session: AppSessionFactory,
) -> None:
    """`uq_org_member_subject` is what makes the first-request upsert safe. Both
    unique indexes are partial on `deleted_at IS NULL` so a removed member never
    permanently blocks re-enrolling the same human."""
    subject = f"anna-{uuid.uuid4()}"
    subject_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"oc8:subject:{subject}")
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    for tenant in (tenant_a, tenant_b):
        async with app_session(tenant) as db:
            db.add(m.OrgMember(tenant_id=tenant, subject=subject, subject_uuid=subject_uuid))
            await db.flush()

    with pytest.raises(IntegrityError, match="uq_org_member_subject"):
        async with app_session(tenant_a) as db:
            db.add(m.OrgMember(tenant_id=tenant_a, subject=subject, subject_uuid=uuid.uuid4()))
            await db.flush()

    async with app_session(tenant_a) as db:
        existing = (
            await db.execute(sa.select(m.OrgMember).where(m.OrgMember.subject == subject))
        ).scalar_one()
        existing.deleted_at = sa.func.now()
        await db.flush()
        db.add(m.OrgMember(tenant_id=tenant_a, subject=subject, subject_uuid=subject_uuid))
        await db.flush()


@pytest.mark.parametrize("table", ["org_member", "org_member_department"])
async def test_both_new_tables_are_row_level_secured(
    app_session: AppSessionFactory, table: str
) -> None:
    """Nothing about the department term replaces tenant isolation. A person and
    a seat are as tenant-scoped as everything else, and this is the check that a
    `CREATE TABLE` in a future migration cannot quietly skip."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        enabled = (
            await db.execute(
                sa.text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
            )
        ).scalar_one()
        policies = (
            await db.execute(
                sa.text("SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": table}
            )
        ).scalar_one()
    assert enabled is True
    assert policies == 1


async def test_a_binding_starts_with_no_member_and_that_is_the_point(
    app_session: AppSessionFactory,
) -> None:
    """No backfill and no grandfathering: `member_id` is nullable, every existing
    binding has NULL, and a binding with NULL decides nothing. Inventing an
    `org_member` per binding would collide with `uq_org_member_subject_uuid` the
    first time that same human authenticated."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        binding = m.ApprovalChannelBinding(
            tenant_id=tenant, channel="telegram", user_id=uuid.uuid4(), external_id="42"
        )
        db.add(binding)
        await db.flush()
        assert binding.member_id is None
