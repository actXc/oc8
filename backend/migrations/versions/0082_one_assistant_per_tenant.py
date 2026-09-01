# backend/migrations/versions/0082_one_assistant_per_tenant.py
"""One tenant Assistant per tenant, enforced by the database.

`agent.assistant.get_or_create_assistant` is check-then-insert, and there are
five concurrent first-call entrypoints for it (GET /assistant, the three
/chat/sessions... routes via `_assistant_visible`, and the Telegram
`bind_from_free_text`). Two tabs on a fresh tenant could each create their own
Assistant AND their own department, after which every later lookup returned an
arbitrary one of them. A partial unique index is what makes the loser of that
race fail loudly enough to be caught and re-read (the savepoint in
`get_or_create_assistant`, mirroring `runtime/intake.enqueue_run`).

Partial, on `is_tenant_assistant AND deleted_at IS NULL`: ordinary agents are
unaffected, and an archived Assistant must not block provisioning a new one.

IF NOT EXISTS / CONCURRENTLY-free on purpose: `agent` is created by
0001_initial.py's live `Base.metadata.create_all()`, so this runs against a
table that may already carry the index on a fresh database, and Alembic runs
migrations inside a transaction (CONCURRENTLY cannot).

Revision ID: 0082
Revises: 0081
Create Date: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0082"
down_revision: str | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Any pre-existing duplicates would make the index creation fail. Keep the
    # oldest -- the same row `_load_assistant`'s `.order_by(created_at)` now
    # answers with -- and archive the rest rather than deleting them, so the
    # runs and tasks hanging off them keep resolving.
    op.execute(
        """
        UPDATE agent SET deleted_at = now()
        WHERE is_tenant_assistant AND deleted_at IS NULL AND id NOT IN (
            SELECT DISTINCT ON (tenant_id) id FROM agent
            WHERE is_tenant_assistant AND deleted_at IS NULL
            ORDER BY tenant_id, created_at, id
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_tenant_assistant "
        "ON agent (tenant_id) WHERE is_tenant_assistant AND deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_agent_tenant_assistant")
