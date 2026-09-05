"""Migration 0087 merges ToolPolicy write/send into one modify field.

Same pattern as tests/runtime/test_task_backfill_predicate.py: the exact SQL
from the migration is copied here as a module constant and run directly
against a seeded department/agent, rather than importing the migration
module (Alembic revision modules have a leading digit in their filename and
are not meant to be imported directly).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _merge_sql(table: str, column: str) -> str:
    # Copied verbatim from migrations/versions/0087_tool_policy_modify_right.py
    # -- keep both copies identical; a drift here is exactly what this test
    # exists to catch (see test_task_backfill_predicate.py's own docstring).
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


_DEPT_SQL = _merge_sql("department", "frame")
_AGENT_SQL = _merge_sql("agent", "narrowing")


async def test_migration_0087_merges_tool_policy_fields(
    app_session: AppSessionFactory,
) -> None:
    tenant_id = uuid.uuid4()
    async with app_session(tenant_id) as db:
        dept = m.Department(
            tenant_id=tenant_id,
            name="D",
            frame={
                "tools": {
                    "github": {"enabled": True, "read": True, "write": False, "send": True},
                    "odoo": {"enabled": True, "read": True, "write": True, "send": False},
                    # A sparse entry with neither write nor send present --
                    # this must NOT gain a modify key from this migration.
                    "sparse": {"enabled": True},
                }
            },
        )
        db.add(dept)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant_id,
            department_id=dept.id,
            name="A",
            narrowing={
                "tools": {
                    "github": {"enabled": True, "read": True, "write": False, "send": False}
                }
            },
        )
        db.add(agent)
        await db.flush()

        await db.execute(text(_DEPT_SQL))
        await db.execute(text(_AGENT_SQL))
        await db.flush()

        await db.refresh(dept)
        await db.refresh(agent)

        assert dept.frame["tools"]["github"] == {"enabled": True, "read": True, "modify": True}
        assert dept.frame["tools"]["odoo"] == {"enabled": True, "read": True, "modify": True}
        assert dept.frame["tools"]["sparse"] == {"enabled": True}
        assert agent.narrowing["tools"]["github"] == {
            "enabled": True,
            "read": True,
            "modify": False,
        }
