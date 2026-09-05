"""Merge ToolPolicy write/send into one modify right.

department.frame["tools"][*] and agent.narrowing["tools"][*] both store a
per-connection policy dict shaped like authz.pdp.ToolPolicy. That dataclass
collapsed its separate write/send booleans into one modify field, and
ToolPolicy.from_json only ever reads a "modify" key now -- so any row still
carrying the old write/send keys silently reads back as modify=False,
regardless of what was actually granted. This merges every such row in
place: modify = write OR send, and the two old keys are dropped. A sparse
entry with neither key present (e.g. {"enabled": true} alone, from a
department-tools cascade that only ever touches "enabled") is left
completely untouched -- there is nothing to merge.

Revision ID: 0087
Revises: 0086
Create Date: 2026-09-05
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0087"
down_revision: str | None = "0086"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def _merge_sql(table: str, column: str) -> str:
    return f"""
UPDATE {table}
SET {column} = jsonb_set(
    {column},
    '{{tools}}',
    (
        SELECT jsonb_object_agg(
            key,
            CASE
                WHEN value ? 'write' OR value ? 'send' THEN
                    (value - 'write' - 'send')
                        || jsonb_build_object(
                            'modify',
                            COALESCE((value->>'write')::boolean, false)
                            OR COALESCE((value->>'send')::boolean, false)
                        )
                ELSE value
                END
        )
        FROM jsonb_each({column}->'tools') AS t(key, value)
    )
)
WHERE jsonb_typeof({column}->'tools') = 'object'
  AND EXISTS (
      SELECT 1 FROM jsonb_each({column}->'tools') AS t(key, value)
      WHERE value ? 'write' OR value ? 'send'
  )
"""


def upgrade() -> None:
    result = op.get_bind().execute(sa.text(_merge_sql("department", "frame")))
    logger.info("0087: merged write/send into modify on %s department frame rows", result.rowcount)
    result = op.get_bind().execute(sa.text(_merge_sql("agent", "narrowing")))
    logger.info("0087: merged write/send into modify on %s agent narrowing rows", result.rowcount)


def downgrade() -> None:
    raise NotImplementedError("write/send are not reconstructible from modify alone")
