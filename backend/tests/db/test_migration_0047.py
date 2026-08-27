"""0047: the shape of a tenant-defined role, asserted against the TABLE.

Everything here is checked against the migrated schema rather than against the
ORM's opinion of it. A CHECK that only lives in `models/core.py` is a comment,
and the claims being made are all of the form "this is unrepresentable", which
is a claim about Postgres:

* an agent-kind row and a human-kind row are different populations of one table,
  and a third kind cannot be written at all;
* a grant or an assignment cannot point at ANOTHER tenant's role, because
  referential integrity checks bypass RLS and the composite key is the only
  place that can be refused;
* deleting a role somebody holds fails loudly instead of demoting them silently;
* §5.2's `resource_id` and `constraint_expr` are deferred in a way that a future
  optimistic afternoon cannot un-defer by writing a row.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _role(tenant: uuid.UUID, name: str, **kw: object) -> m.Role:
    return m.Role(tenant_id=tenant, name=name, **kw)


async def test_role_permission_is_row_level_secured(app_session: AppSessionFactory) -> None:
    """Nothing about tenant-defined authority replaces tenant isolation. A
    `CREATE TABLE` in a later migration can silently skip this; the tenant whose
    grants become readable cannot."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        enabled = (
            await db.execute(
                sa.text("SELECT relrowsecurity FROM pg_class WHERE relname = 'role_permission'")
            )
        ).scalar_one()
        policies = (
            await db.execute(
                sa.text("SELECT count(*) FROM pg_policies WHERE tablename = 'role_permission'")
            )
        ).scalar_one()
    assert enabled is True
    assert policies == 1


async def test_a_grant_written_under_one_tenant_is_invisible_under_another(
    app_session: AppSessionFactory,
) -> None:
    """The role's NAME is a tenant's own word and its grants are its own
    configuration. Both are as tenant-scoped as everything else."""
    ours, theirs = uuid.uuid4(), uuid.uuid4()
    async with app_session(ours) as db:
        role = _role(ours, "Freigabe Vertrieb")
        db.add(role)
        await db.flush()
        db.add(m.RolePermission(tenant_id=ours, role_id=role.id, permission="approval:decide"))
        await db.flush()
        role_id = role.id

    async with app_session(theirs) as db:
        assert (
            await db.execute(sa.select(m.Role).where(m.Role.id == role_id))
        ).scalar_one_or_none() is None
        visible = (
            await db.execute(
                sa.select(sa.func.count())
                .select_from(m.RolePermission)
                .where(m.RolePermission.role_id == role_id)
            )
        ).scalar_one()
    assert visible == 0


async def test_a_role_is_human_unless_it_says_otherwise(app_session: AppSessionFactory) -> None:
    """'human' is the default, and it is the safe one of the two: a row that
    defaults wrong grants an AGENT nothing, where the opposite default would
    grant a person's role every tool right the moment it was pointed at."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(_role(tenant, "Praktikant"))
        await db.flush()
        kind = (
            await db.execute(
                sa.text("SELECT kind FROM role WHERE tenant_id = :t AND name = 'Praktikant'"),
                {"t": str(tenant)},
            )
        ).scalar_one()
    assert kind == "human"


async def test_a_third_kind_of_role_is_unrepresentable(app_session: AppSessionFactory) -> None:
    """Two resolvers read this table and each takes exactly one kind. A third
    value would be a population neither of them admits -- so it fails at the
    moment of the mistake rather than as an agent that silently stopped working.
    """
    tenant = uuid.uuid4()
    with pytest.raises(IntegrityError, match="ck_role_kind"):
        async with app_session(tenant) as db:
            db.add(_role(tenant, "Etwas", kind="service"))
            await db.flush()


async def test_one_role_name_per_tenant_case_insensitively(
    app_session: AppSessionFactory,
) -> None:
    """The name is what an administrator types and what the audit trail quotes,
    so `Freigabe` and `freigabe` being two roles is a support call waiting to
    happen. Across tenants the same name is of course fine."""
    ours, theirs = uuid.uuid4(), uuid.uuid4()
    async with app_session(ours) as db:
        db.add(_role(ours, "Freigabe"))
        await db.flush()
    async with app_session(theirs) as db:
        db.add(_role(theirs, "Freigabe"))
        await db.flush()

    with pytest.raises(IntegrityError, match="uq_role_tenant_name"):
        async with app_session(ours) as db:
            db.add(_role(ours, "FREIGABE"))
            await db.flush()


async def test_deleting_a_role_does_not_free_its_name(app_session: AppSessionFactory) -> None:
    """The index is UNCONDITIONAL, not partial on `deleted_at IS NULL`, and this
    is the test that says why. Renaming is refused, so delete-and-recreate is the
    sanctioned way to change a name -- and a freed name means the next role to
    take it silently inherits every mention of the old one in the audit trail.
    The name is tombstoned with the row."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        role = _role(tenant, "Abteilungsleitung")
        db.add(role)
        await db.flush()
        role.deleted_at = sa.func.now()
        await db.flush()

    with pytest.raises(IntegrityError, match="uq_role_tenant_name"):
        async with app_session(tenant) as db:
            db.add(_role(tenant, "abteilungsleitung"))
            await db.flush()


async def test_a_member_may_hold_no_role_at_all(app_session: AppSessionFactory) -> None:
    """NULL is not "no permissions" -- it is "the token decides", which is every
    human on every live tenant today. The foreign key must therefore not fire on
    it, or the seat slice's first-request upsert would 500."""
    tenant = uuid.uuid4()
    subject = f"anna-{uuid.uuid4()}"
    async with app_session(tenant) as db:
        member = m.OrgMember(tenant_id=tenant, subject=subject, subject_uuid=uuid.uuid4())
        db.add(member)
        await db.flush()
        assert member.role_id is None


async def test_a_role_from_another_tenant_cannot_be_assigned(
    app_session: AppSessionFactory,
) -> None:
    """The composite foreign key, and the reason it is `(tenant_id, id)` rather
    than `(id)`. Referential integrity checks bypass RLS, so a bare FK on `id`
    would happily let one tenant's member point at another tenant's role -- and
    that role's grants would then resolve for them."""
    ours, theirs = uuid.uuid4(), uuid.uuid4()
    async with app_session(theirs) as db:
        foreign = _role(theirs, "Ihre Rolle")
        db.add(foreign)
        await db.flush()
        foreign_id = foreign.id

    with pytest.raises(IntegrityError, match="fk_org_member_role"):
        async with app_session(ours) as db:
            db.add(
                m.OrgMember(
                    tenant_id=ours,
                    subject=f"anna-{uuid.uuid4()}",
                    subject_uuid=uuid.uuid4(),
                    role_id=foreign_id,
                )
            )
            await db.flush()


async def test_a_role_somebody_holds_cannot_be_deleted_out_from_under_them(
    app_session: AppSessionFactory,
) -> None:
    """ON DELETE RESTRICT, and it is the load-bearing half of the deletion story.
    SET NULL would restore every holder to their TOKEN role, which on the live
    system means silently restoring forty demoted people to `org_admin`; CASCADE
    would delete the people. A refusal the endpoint turns into a 409 naming the
    holders is the only one of the three an administrator can act on."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        role = _role(tenant, "Mitarbeiter")
        db.add(role)
        await db.flush()
        db.add(
            m.OrgMember(
                tenant_id=tenant,
                subject=f"anna-{uuid.uuid4()}",
                subject_uuid=uuid.uuid4(),
                role_id=role.id,
            )
        )
        await db.flush()
        role_id = role.id

    with pytest.raises(IntegrityError, match="fk_org_member_role"):
        async with app_session(tenant) as db:
            await db.execute(sa.delete(m.Role).where(m.Role.id == role_id))
            await db.flush()


async def test_a_roles_grants_go_when_the_role_does(app_session: AppSessionFactory) -> None:
    """CASCADE on the grants and RESTRICT on the holders, which is the right pair:
    a grant has no meaning without its role, a PERSON does."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        role = _role(tenant, "Freigabe DACH")
        db.add(role)
        await db.flush()
        for permission in ("approval:view", "approval:decide"):
            db.add(m.RolePermission(tenant_id=tenant, role_id=role.id, permission=permission))
        await db.flush()
        role_id = role.id

    async with app_session(tenant) as db:
        await db.execute(sa.delete(m.Role).where(m.Role.id == role_id))
        await db.flush()
        left = (
            await db.execute(
                sa.select(sa.func.count())
                .select_from(m.RolePermission)
                .where(m.RolePermission.role_id == role_id)
            )
        ).scalar_one()
    assert left == 0


async def test_a_permission_is_granted_once_or_not_at_all(
    app_session: AppSessionFactory,
) -> None:
    """There is no second kind of grant, so a duplicate row is a bug in the
    writer -- and one that would make `PUT /roles/{id}`'s delete-and-re-insert
    look like it worked while leaving a ghost behind."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        role = _role(tenant, "Doppelt")
        db.add(role)
        await db.flush()
        db.add(m.RolePermission(tenant_id=tenant, role_id=role.id, permission="approval:view"))
        await db.flush()
        role_id = role.id

    with pytest.raises(IntegrityError, match="uq_role_permission"):
        async with app_session(tenant) as db:
            db.add(m.RolePermission(tenant_id=tenant, role_id=role_id, permission="approval:view"))
            await db.flush()


async def test_a_permission_row_cannot_carry_an_object_or_a_constraint(
    app_session: AppSessionFactory,
) -> None:
    """§5.2's other two dimensions, deferred by Postgres rather than by a comment.

    Nothing in this system evaluates `resource_id` or `constraint_expr`, so a row
    carrying either is a grant that looks enforced and is not -- the most
    dangerous kind of authorization row there is. Zero such rows exist today, and
    these two CHECKs are what keeps that true through the next importer, the next
    migration and the next optimistic afternoon. Dropping them is the whole of
    the migration that turns the feature on.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        role = _role(tenant, "Tupel")
        db.add(role)
        await db.flush()
        role_id = role.id
        # The empty shape still inserts: the deferral is of two dimensions, not
        # of the table.
        db.add(
            m.Permission(
                tenant_id=tenant,
                role_id=role_id,
                resource_type="knowledge",
                action="view",
                constraint_expr={},
            )
        )
        await db.flush()

    with pytest.raises(IntegrityError, match="ck_permission_no_object"):
        async with app_session(tenant) as db:
            db.add(
                m.Permission(
                    tenant_id=tenant,
                    role_id=role_id,
                    resource_type="knowledge",
                    resource_id=uuid.uuid4(),
                    action="view",
                    constraint_expr={},
                )
            )
            await db.flush()

    with pytest.raises(IntegrityError, match="ck_permission_no_constraint"):
        async with app_session(tenant) as db:
            db.add(
                m.Permission(
                    tenant_id=tenant,
                    role_id=role_id,
                    resource_type="knowledge",
                    action="view",
                    constraint_expr={"department": "vertrieb"},
                )
            )
            await db.flush()
