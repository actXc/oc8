"""Backdate the 6 baseline model_price seed rows' effective_from to a
far-past sentinel (Cost Center design, 2026-08-19 spec Part A -- Task 14
final E2E verification finding).

Why this exists: migrations 0060 (model_price.py) and 0062
(correct_claude45_price_pattern.py) both INSERT their seed rows without an
explicit effective_from, so it defaults to the column's `DEFAULT now()` --
whichever moment the migration happens to run in a given database.
price_as_of() requires effective_from <= usage_bucket_end, so any
TokenUsageRecord from *before* a database's 0060/0062 apply moment can
never match these seed prices and permanently prices at $0/unknown. This
reproduced live in Task 14's E2E verification against 4 days of real
pre-existing dev-DB usage -- the exact symptom the whole Cost Center plan
exists to fix, just via a different mechanism (missing price-effective-date
coverage instead of a stale hardcoded table).

Why a direct UPDATE, breaking the append-only convention every other price
migration in this plan follows: this is not a price CHANGE in the domain
sense -- the price values are untouched -- it is a correction to WRONGLY-SET
METADATA on seed data that was never a real admin-initiated pricing event to
begin with. An INSERT (the append-only convention for a genuine price
change) cannot fix this: the "longest-pattern-then-most-recent-effective_from
wins" rule in price_as_of() means a new row can only become "current" by
having a LATER effective_from than what's already there, which does nothing
to help OLD usage match it -- the whole problem is that the existing rows'
effective_from is already too late. Only correcting the existing rows'
effective_from in place fixes historical matching. This is a one-time
migration-authoring correction, not a precedent for future genuine price
edits, which must keep using POST /model-prices (an INSERT, no
effective_from override -- see api/v1/model_prices.py).

Scope, precisely: only the 6 baseline (provider, model_pattern) seed rows
from 0060/0062, matched by their EXACT original seed price values (not just
provider/model_pattern), so this can never touch a row an admin has since
edited through the real pricing panel/API -- a same-pattern price edit
always carries a different price_in/price_out (or, on a rare identical-value
edit, a later effective_from that a same-value idempotent re-run of this
migration would simply re-affirm as still-2020, which is harmless since a
genuinely new admin-set price always gets its own later effective_from row
per the append-only API and so is never itself matched by this migration's
WHERE clause). Deliberately excludes 0062's decoy `claude45sonnet`
(active=false) row and the *original*, still-active `claude45sonnet` row
from 0060 (the wrong, pre-correction pattern) -- see the note on 0062's own
docstring below.

Note on 0062's docstring: 0062 says it "deactivates the wrong [claude45sonnet]
row" -- what it actually does is INSERT a brand-new claude45sonnet row with
active=false, never touching the original 0060 claude45sonnet row (which
stays active=true, untouched, forever). This is functionally harmless
(the wrong pattern "claude45sonnet" never substring-matches a real
normalized Claude 4.x model id, e.g. "claudesonnet4520250929" -- the
character order doesn't line up), so it's left alone here; this note exists
only to correct 0062's docstring wording without editing an already-merged
migration file in place.

Revision ID: 0065
Revises: 0064
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

# (provider, model_pattern, price_in_usd_per_1m, price_out_usd_per_1m) for
# the 6 baseline seed rows -- exact values from migrations 0060 and 0062.
_SEED_ROWS: list[tuple[str, str, float, float]] = [
    ("anthropic", "claude35sonnet", 3.0, 15.0),
    ("anthropic", "claude35haiku", 0.8, 4.0),
    ("anthropic", "claudesonnet45", 3.0, 15.0),  # from 0062, not 0060
    ("openai", "gpt4omini", 0.15, 0.6),
    ("openai", "gpt4o", 2.5, 10.0),
    ("openai_compatible", "mistrallarge", 2.0, 6.0),
]

_BACKDATE_TO = "2020-01-01T00:00:00Z"


_UPGRADE_SQL = sa.text(
    """
    UPDATE model_price
    SET effective_from = :backdate_to
    WHERE provider = :provider
      AND model_pattern = :pattern
      AND price_in_usd_per_1m = :price_in
      AND price_out_usd_per_1m = :price_out
      AND active = true
    """
)

_DOWNGRADE_SQL = sa.text(
    """
    UPDATE model_price
    SET effective_from = now()
    WHERE provider = :provider
      AND model_pattern = :pattern
      AND price_in_usd_per_1m = :price_in
      AND price_out_usd_per_1m = :price_out
      AND active = true
      AND effective_from = :backdate_to
    """
)


def upgrade() -> None:
    bind = op.get_bind()
    for provider, pattern, price_in, price_out in _SEED_ROWS:
        result = bind.execute(
            _UPGRADE_SQL,
            {
                "provider": provider,
                "pattern": pattern,
                "price_in": price_in,
                "price_out": price_out,
                "backdate_to": _BACKDATE_TO,
            },
        )
        logger.info(
            "0065: backdated %s row(s) for %s/%s to %s",
            result.rowcount,
            provider,
            pattern,
            _BACKDATE_TO,
        )


def downgrade() -> None:
    # The original wrong timestamps (whatever `now()` happened to be when
    # 0060/0062 first ran in this database) aren't meaningful to restore --
    # they were the bug, not a value worth reversing to. Best-effort/no-op
    # in spirit: put these rows back to "effective as of right now", which
    # matches this migration never having backdated them at all from this
    # point forward. Must not error, per alembic downgrade -1 requirements.
    bind = op.get_bind()
    for provider, pattern, price_in, price_out in _SEED_ROWS:
        bind.execute(
            _DOWNGRADE_SQL,
            {
                "provider": provider,
                "pattern": pattern,
                "price_in": price_in,
                "price_out": price_out,
                "backdate_to": _BACKDATE_TO,
            },
        )
