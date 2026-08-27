"""close tasks left open behind runs that already ended

The repair half of the fix in `RunRepository.transition`. Until now only the
in-process engine closed a task; every run that went through a container
runtime, and every run failed by the reconciler or by the executor catching an
exception, left its task row open for ever. 66 of them were sitting in
`in_progress` on the live system when this was found -- across all three agents,
the oldest four days old -- each one telling the board that work nobody is doing
is still being done.

The predicate matches the runtime rule exactly, and is deliberately narrow:

* the task has not already settled (`done` / `failed` / `budget_exceeded` are
  left alone -- `budget_exceeded` in particular is a reason, not just an ending);
* at least one run of it exists -- a task with no run at all is not evidence of
  anything, and unknown is not the same as done;
* and NONE of its runs is still in a live state.

The verdict is derived rather than assumed: a task whose runs all finished
`done` becomes `done`, anything else `failed`. In practice every row this
touches on the live system is behind a failed run, but writing `failed` blindly
would have been a guess where the data holds an answer.

Revision ID: 0044
Revises: 0043
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LIVE_RUN = "('queued','running','waiting_for_input','waiting_for_approval')"
_SETTLED_TASK = "('done','failed','budget_exceeded')"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE task t SET state = CASE
            WHEN NOT EXISTS (
                SELECT 1 FROM agent_run r
                WHERE r.task_id = t.id AND r.tenant_id = t.tenant_id
                  AND r.state <> 'done'
            ) THEN 'done'
            ELSE 'failed'
        END
        WHERE t.state NOT IN {_SETTLED_TASK}
          AND EXISTS (
              SELECT 1 FROM agent_run r
              WHERE r.task_id = t.id AND r.tenant_id = t.tenant_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM agent_run r
              WHERE r.task_id = t.id AND r.tenant_id = t.tenant_id
                AND r.state IN {_LIVE_RUN}
          )
        """
    )


def downgrade() -> None:
    """Deliberately a no-op, and said out loud rather than left to look reversible.

    The previous value is not recoverable: a task that was `in_progress` and one
    that was `waiting_for_approval` both become `failed` here, and nothing
    records which it was. Re-opening every closed task would be a far worse
    guess than leaving them closed -- it would put finished work back on the
    board.
    """
