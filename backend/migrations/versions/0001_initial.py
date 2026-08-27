"""initial schema with RLS

Revision ID: 0001
Revises:
Create Date: 2026-07-09
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from oc8 import models  # noqa: F401  (registers all tables)
from oc8.db.base import Base

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables whose row visibility is keyed on their own id (the tenant root) rather
# than a tenant_id column.
_ROOT_TABLE = "organization"
# Append-only tables the runtime role may insert into and read, but never mutate.
_IMMUTABLE_TABLES = ("audit_event", "token_usage_record")

# The exact table set that existed at revision 0001. This migration is pinned to
# this frozen snapshot on purpose: tables added by later revisions are created by
# their own migration. We must NOT let a live `Base.metadata.create_all()` pick up
# whatever models happen to be registered on `Base.metadata` now, or a model added
# in a future revision would get created here on fresh DBs — before its own
# migration runs — diverging fresh installs from incremental deploys.
_TABLES_AT_0001 = frozenset(
    {
        "activity_event",
        "agent",
        "approval_request",
        "audit_event",
        "data_source",
        "department",
        "ingestion_job",
        "integration",
        "kb_chunk",
        "knowledge_base",
        "knowledge_grant",
        "mcp_connection",
        "memory_record",
        "memory_store",
        "model_config",
        "organization",
        "permission",
        "role",
        "skill",
        "skill_assignment",
        "skill_version",
        "task",
        "token_usage_record",
    }
)


def _snapshot_tables() -> list:
    return [t for t in Base.metadata.sorted_tables if t.name in _TABLES_AT_0001]


def upgrade() -> None:
    bind = op.get_bind()
    tables = _snapshot_tables()
    Base.metadata.create_all(bind=bind, tables=tables)

    for table in tables:
        name = table.name
        if name == _ROOT_TABLE:
            op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {name} "
                "USING (id = current_setting('app.tenant_id', true)::uuid)"
            )
            continue
        if "tenant_id" not in table.columns:
            continue
        op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {name} "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )

    for table in _IMMUTABLE_TABLES:
        op.execute(f"REVOKE UPDATE, DELETE ON {table} FROM oc8_app")


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, tables=_snapshot_tables())
