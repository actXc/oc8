"""Migration 0027's seeding of verified_count / break_kind.

No pre-existing checkpoint can carry a truncation break -- see the migration's
module docstring for why (0026 and 0027 ship in the same release, so no
database was ever verified by the 0026-era code whose unconditional
max_seen_seq advance could produce a false "truncation" signature). Every
pre-existing broken checkpoint is therefore labelled hash_mismatch and
verified_count is seeded the same COUNT(seq <= max_seen_seq) unconditionally,
regardless of how last_seq relates to max_seen_seq -- that relationship is no
longer a discriminator of anything.
"""

from __future__ import annotations

import importlib.util
import pathlib
import uuid

import pytest
import sqlalchemy as sa
from tests.conftest import AppSessionFactory

from oc8.audit.chain import append_event

pytestmark = pytest.mark.asyncio

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "0027_audit_checkpoint_verified_count.py"
)


def _seed_sql() -> tuple[str, str]:
    spec = importlib.util.spec_from_file_location("mig0027", _MIGRATION)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.LABEL_SQL, mod.SEED_SQL


async def _emit(app_session: AppSessionFactory, tenant: uuid.UUID, n: int) -> None:
    async with app_session(tenant) as s:
        for i in range(n):
            await append_event(
                s,
                tenant_id=tenant,
                actor_type="system",
                actor_id=None,
                category="test",
                action=f"m.{i}",
            )


def _insert_legacy_checkpoint(
    conn: sa.Connection, tenant: uuid.UUID, *, last_seq: int, max_seen_seq: int, status: str
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO audit_chain_checkpoint "
            "(id, tenant_id, last_seq, last_hash, status, max_seen_seq, verified_count, "
            " break_kind, verified_at) "
            "VALUES (:id, :t, :ls, :h, :st, :mss, 0, NULL, now())"
        ),
        {
            "id": uuid.uuid4(),
            "t": str(tenant),
            "ls": last_seq,
            "h": b"\x00" * 32,
            "st": status,
            "mss": max_seen_seq,
        },
    )


async def test_seed_backfills_a_clean_checkpoint_from_the_live_count(
    app_session: AppSessionFactory, pg_url: str
) -> None:
    tenant = uuid.uuid4()
    await _emit(app_session, tenant, 3)
    label, seed = _seed_sql()
    engine = sa.create_engine(pg_url)
    with engine.begin() as conn:
        tail = conn.execute(
            sa.text("SELECT max(seq) FROM audit_event WHERE tenant_id = :t"), {"t": str(tenant)}
        ).scalar_one()
        _insert_legacy_checkpoint(conn, tenant, last_seq=tail, max_seen_seq=tail, status="ok")
        conn.execute(sa.text(label))
        conn.execute(sa.text(seed))
        row = conn.execute(
            sa.text(
                "SELECT verified_count, break_kind FROM audit_chain_checkpoint WHERE tenant_id = :t"
            ),
            {"t": str(tenant)},
        ).one()
    engine.dispose()
    assert row.verified_count == 3
    assert row.break_kind is None


async def test_seed_labels_a_pre_existing_break_hash_mismatch_regardless_of_the_old_signature(
    app_session: AppSessionFactory, pg_url: str
) -> None:
    """`last_seq < max_seen_seq` used to be treated as a "this is a
    truncation" signature. It never discriminated anything (the 0026-era code
    advanced max_seen_seq unconditionally, so a hash-mismatch break produces
    the exact same relationship once one more event lands) -- so a broken
    checkpoint carrying it is labelled hash_mismatch and seeded the live
    count, exactly like one where last_seq == max_seen_seq."""
    tenant = uuid.uuid4()
    await _emit(app_session, tenant, 3)
    label, seed = _seed_sql()
    engine = sa.create_engine(pg_url)
    with engine.begin() as conn:
        tail = conn.execute(
            sa.text("SELECT max(seq) FROM audit_event WHERE tenant_id = :t"), {"t": str(tenant)}
        ).scalar_one()
        # The old (removed) "truncation signature": last_seq retreated below
        # the mark. Nothing is actually missing -- all 3 rows are present.
        _insert_legacy_checkpoint(
            conn, tenant, last_seq=tail, max_seen_seq=tail + 10, status="broken"
        )
        conn.execute(sa.text(label))
        conn.execute(sa.text(seed))
        row = conn.execute(
            sa.text(
                "SELECT verified_count, break_kind FROM audit_chain_checkpoint WHERE tenant_id = :t"
            ),
            {"t": str(tenant)},
        ).one()
    engine.dispose()
    assert row.break_kind == "hash_mismatch"
    assert row.verified_count == 3


async def test_seed_labels_a_pre_existing_break_hash_mismatch_at_the_mark_too(
    app_session: AppSessionFactory, pg_url: str
) -> None:
    tenant = uuid.uuid4()
    await _emit(app_session, tenant, 3)
    label, seed = _seed_sql()
    engine = sa.create_engine(pg_url)
    with engine.begin() as conn:
        tail = conn.execute(
            sa.text("SELECT max(seq) FROM audit_event WHERE tenant_id = :t"), {"t": str(tenant)}
        ).scalar_one()
        _insert_legacy_checkpoint(conn, tenant, last_seq=tail, max_seen_seq=tail, status="broken")
        conn.execute(sa.text(label))
        conn.execute(sa.text(seed))
        row = conn.execute(
            sa.text(
                "SELECT verified_count, break_kind FROM audit_chain_checkpoint WHERE tenant_id = :t"
            ),
            {"t": str(tenant)},
        ).one()
    engine.dispose()
    assert row.break_kind == "hash_mismatch"
    assert row.verified_count == 3
