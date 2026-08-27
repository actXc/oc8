"""Correct Task 1's wrong claude45sonnet price pattern to claudesonnet45 --
real Claude 4.x model IDs (e.g. claude-sonnet-4-5-20250929) normalize to
name-then-version order, not the 3.5-era version-then-name order the wrong
pattern was copy-pasted from. Append-only: inserts a new correct row and
deactivates the wrong one, never edits either in place.

Revision ID: 0062
Revises: 0061
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO model_price
          (id, provider, model_pattern, price_in_usd_per_1m, price_out_usd_per_1m)
        VALUES
          (gen_random_uuid(), 'anthropic', 'claudesonnet45', 3.0, 15.0)
    """)
    op.execute("""
        INSERT INTO model_price
          (id, provider, model_pattern, price_in_usd_per_1m, price_out_usd_per_1m, active)
        VALUES
          (gen_random_uuid(), 'anthropic', 'claude45sonnet', 3.0, 15.0, false)
    """)


def downgrade() -> None:
    op.execute(
        "DELETE FROM model_price WHERE provider = 'anthropic' AND model_pattern = 'claudesonnet45'"
    )
    op.execute("""
        DELETE FROM model_price
        WHERE provider = 'anthropic' AND model_pattern = 'claude45sonnet' AND active = false
    """)
