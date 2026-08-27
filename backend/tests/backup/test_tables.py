"""Table-set derivation, denylist, and FK dependency order (design doc §2.1,
§5.2.1)."""

from __future__ import annotations

import uuid

from sqlalchemy import text

from oc8.backup.tables import EXCLUDED_TABLES, dependency_order, exported_tables, table_by_name
from oc8.db.base import Base
from tests.conftest import AppSessionFactory


def test_excluded_tables_all_exist() -> None:
    """A renamed table breaks the build rather than silently re-entering the export."""
    known = set(Base.metadata.tables.keys())
    assert EXCLUDED_TABLES <= known


def test_exported_tables_excludes_the_denylist() -> None:
    exported = exported_tables()
    assert exported.isdisjoint(EXCLUDED_TABLES)
    assert "agent_run" in exported and "run_message" in exported and "task" in exported
    # 54 TenantMixin tables minus the 6-name denylist (totp_credential added
    # by the standalone 2FA design's migration 0071).
    assert len(exported) == 48


def test_table_by_name_returns_a_real_table() -> None:
    table = table_by_name("agent")
    assert "tenant_id" in table.columns


async def test_dependency_order_places_role_before_role_permission_and_org_member(
    app_session: AppSessionFactory,
) -> None:
    tables = exported_tables()
    async with app_session(uuid.uuid4()) as s:
        order = await dependency_order(s, tables)
    assert order.index("role") < order.index("role_permission")
    assert order.index("role") < order.index("org_member")
    # every table exactly once
    assert sorted(order) == sorted(tables)


async def test_dependency_order_deletion_is_the_reverse(
    app_session: AppSessionFactory,
) -> None:
    tables = exported_tables()
    async with app_session(uuid.uuid4()) as s:
        order = await dependency_order(s, tables)
    reverse = list(reversed(order))
    assert reverse.index("role_permission") < reverse.index("role")


async def test_every_table_the_app_may_not_delete_is_excluded_from_the_export(
    app_session: AppSessionFactory,
) -> None:
    """A table the runtime role may not DELETE cannot take part in a restore.

    Restore replaces a company by deleting its rows and inserting the
    archive's. Against an undeletable table the best it could do is merge,
    leaving a company whose history is the union of two -- so
    immutable-by-grant must imply excluded.

    Asks POSTGRES, not the migrations. The first version of this test parsed
    migration 0001's `_IMMUTABLE_TABLES` tuple and was weaker than it claimed:
    migration 0028 had already revoked DELETE on `audit_chain_checkpoint`
    outside that tuple, so the "third immutable table" it promised to catch
    already existed and went unseen. A second version grepped the migrations
    and could not resolve the f-string variables they interpolate. The live
    grant is the only form that no future migration style can outflank.
    """
    async with app_session(uuid.uuid4()) as s:
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT table_name FROM information_schema.table_privileges "
                        "WHERE grantee = 'oc8_app' AND privilege_type = 'DELETE' "
                        "AND table_schema = 'public'"
                    )
                )
            )
            .scalars()
            .all()
        )

    deletable = set(rows)
    assert deletable, "oc8_app may DELETE nothing at all -- the query has rotted"
    exported_but_undeletable = exported_tables() - deletable
    assert not exported_but_undeletable, (
        "tables the app may not DELETE, yet the restore would try to: "
        f"{sorted(exported_but_undeletable)}"
    )
