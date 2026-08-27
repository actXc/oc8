"""model_price -- global, append-only price table (Cost Center design, 2026-08-19 spec Part A).

Revision ID: 0060
Revises: 0059
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Global, not tenant-scoped: provider token prices are identical for every
    # tenant (confirmed with the user during design, see the 2026-08-19 spec's
    # Global Constraints). No RLS -- every tenant reads and, if permitted,
    # writes the same rows.
    op.execute("""
        CREATE TABLE model_price (
          id uuid PRIMARY KEY,
          provider text NOT NULL,
          model_pattern text NOT NULL,
          price_in_usd_per_1m numeric NOT NULL,
          price_out_usd_per_1m numeric NOT NULL,
          effective_from timestamptz NOT NULL DEFAULT now(),
          active boolean NOT NULL DEFAULT true,
          created_by uuid,
          created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX ix_model_price_provider_pattern_effective "
        "ON model_price (provider, model_pattern, effective_from)"
    )
    # Seed with today's hardcoded 5 entries (metering/pricing.py's old _PRICES)
    # plus the currently-missing current-generation model that caused the
    # original bug (claude-sonnet-4-5-20250929, matched via the "claude45sonnet"
    # pattern -- most-specific-first matching means this must not collide with
    # "claude35sonnet").
    op.execute("""
        INSERT INTO model_price
          (id, provider, model_pattern, price_in_usd_per_1m, price_out_usd_per_1m)
        VALUES
          (gen_random_uuid(), 'anthropic', 'claude35sonnet', 3.0, 15.0),
          (gen_random_uuid(), 'anthropic', 'claude35haiku', 0.8, 4.0),
          (gen_random_uuid(), 'anthropic', 'claude45sonnet', 3.0, 15.0),
          (gen_random_uuid(), 'openai', 'gpt4omini', 0.15, 0.6),
          (gen_random_uuid(), 'openai', 'gpt4o', 2.5, 10.0),
          (gen_random_uuid(), 'openai_compatible', 'mistrallarge', 2.0, 6.0)
    """)


def downgrade() -> None:
    op.drop_table("model_price")
