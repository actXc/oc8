"""imported_skill_file table with RLS -- references/assets/scripts bundled
with a directly-imported (non-capa) skill, pinned to its SkillVersion.

Revision ID: 0085
Revises: 0084
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0085"
down_revision: str | None = "0084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = dict(server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "imported_skill_file",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("skill_version_id", sa.Uuid(), nullable=False),
        sa.Column("rel_path", sa.Text(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.UniqueConstraint(
            "skill_version_id", "rel_path", name="uq_imported_skill_file_version_path"
        ),
    )
    op.create_index(
        "ix_imported_skill_file_tenant_id", "imported_skill_file", ["tenant_id"]
    )
    op.create_index(
        "ix_imported_skill_file_skill_version_id",
        "imported_skill_file",
        ["skill_version_id"],
    )
    op.execute("ALTER TABLE imported_skill_file ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON imported_skill_file "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.drop_table("imported_skill_file")
