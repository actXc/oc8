"""approval_channel_binding — which messenger account is which oc8 user

A chat id is not a user and a phone number proves nothing, so §5.6 makes the
link explicit: an authenticated user generates a one-time code in the UI and
sends it to the bot, which exchanges it for a binding. Inferring the link from a
phone number would mean anybody who learns the bot's name and spoofs a number
could approve a refund.

Two states in one table, told apart by `external_id`:

    external_id NULL     -> a code issued, not yet redeemed (expires)
    external_id set      -> a live binding (revocable)

One table rather than two because a code IS a binding waiting for its account:
splitting them would need a transaction to move a row between them, and the
partial unique index below already keeps the two states apart.

Revision ID: 0042
Revises: 0041
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IF NOT EXISTS throughout: test databases are built from the live ORM
    # models, so this migration runs against a schema that already has the table.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_channel_binding (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            channel text NOT NULL,
            user_id uuid NOT NULL,
            external_id text,
            code text,
            code_expires_at timestamptz,
            revoked_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_acb_tenant_id ON approval_channel_binding (tenant_id)"
    )
    # The lookup every inbound message does: channel + external id -> user.
    # Partial, so the many unredeemed codes never enter it.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_acb_external ON approval_channel_binding "
        "(tenant_id, channel, external_id) WHERE external_id IS NOT NULL AND revoked_at IS NULL"
    )
    # Unique across tenants, although redemption is tenant-scoped (a tenant
    # installs its OWN bot, so the inbound call already knows whose it is). The
    # index is here so a mistyped code can never collide with a live one
    # belonging to somebody else -- cheap, and the alternative is a bug nobody
    # would find by testing one tenant.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_acb_code ON approval_channel_binding (code) "
        "WHERE code IS NOT NULL"
    )
    op.execute("ALTER TABLE approval_channel_binding ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON approval_channel_binding")
    op.execute(
        "CREATE POLICY tenant_isolation ON approval_channel_binding "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS approval_channel_binding")
