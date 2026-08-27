"""model_config gains credential_id -- lets a tenant bind a specific
Credential to one ModelConfig instead of always resolving the tenant-wide
"first credential of this provider's type" convention (resolve_model_key /
resolve_model_base_url). Same nullable, unconstrained, indexed shape as
mcp_connection.credential_id (migration 0069) -- this is that same design
applied to LLM provider keys, enabling multiple accounts of one provider
(e.g. two OpenAI keys) each bound to its own ModelConfig.

Revision ID: 0074
Revises: 0073
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0074"
down_revision: str | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # model_config is in migration 0001's create_all frozen set: the model
    # declares credential_id now, so a fresh DB already has the column by the
    # time this migration runs. IF NOT EXISTS converges the fresh-install
    # path with the incremental-migration path.
    op.execute("ALTER TABLE model_config ADD COLUMN IF NOT EXISTS credential_id uuid")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_model_config_credential_id "
        "ON model_config (credential_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_model_config_credential_id")
    op.execute("ALTER TABLE model_config DROP COLUMN IF EXISTS credential_id")
