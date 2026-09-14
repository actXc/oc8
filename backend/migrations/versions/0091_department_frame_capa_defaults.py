"""Add Department.frame_capa_defaults.

A snapshot of `frame` exactly as the department_template plugin declared it
at instantiation time, never touched again afterwards -- the "capa_default"
rung of the guardrails Source/provenance ladder (see the column's own
docstring in models/core.py). Every department that already exists predates
this column by definition, so it is backfilled here from that department's
OWN CURRENT `frame` -- not left NULL -- because NULL would make Source
report every key on every pre-existing department as "department" (hand-
edited), which is a worse and more visible regression ("everything looks
customized") than the backfill's real but narrower inaccuracy: a department
customized before this migration ran will show its already-made edits as
still "capa_default" until edited again. New departments instantiated after
this migration get an accurate snapshot going forward regardless, since
`capas/service.py::instantiate_department` populates this column itself.

Revision ID: 0091
Revises: 0090
Create Date: 2026-09-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0091"
down_revision: str | None = "0090"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE department ADD COLUMN IF NOT EXISTS frame_capa_defaults jsonb")
    op.execute(
        "UPDATE department SET frame_capa_defaults = frame WHERE frame_capa_defaults IS NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE department DROP COLUMN IF EXISTS frame_capa_defaults")
