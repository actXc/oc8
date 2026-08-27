"""Budget: add dollar_budget_usd + dollar_reference_{provider,model} --
informational fields only (Cost Center design, 2026-08-19 spec Part B).
Enforcement stays 100% token-based; these are read-only display fields set
once at write time by the $ -> tokens conversion.

Revision ID: 0063
Revises: 0062
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0063"
down_revision: str | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE budget ADD COLUMN dollar_budget_usd numeric")
    op.execute("ALTER TABLE budget ADD COLUMN dollar_reference_provider text")
    op.execute("ALTER TABLE budget ADD COLUMN dollar_reference_model text")


def downgrade() -> None:
    op.execute("ALTER TABLE budget DROP COLUMN dollar_budget_usd")
    op.execute("ALTER TABLE budget DROP COLUMN dollar_reference_provider")
    op.execute("ALTER TABLE budget DROP COLUMN dollar_reference_model")
