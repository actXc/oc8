"""Add password_hash column to org_member for local password authentication in Community.

Per WP-B spec (§4.1 and §8), Community deployments may use local password authentication
as an alternative to OIDC. The password_hash column stores Argon2id hashes for the initial
admin user created via POST /auth/setup.

NULL value means no local password (user authenticates via configured IdP, e.g., Keycloak).

Revision ID: 0052
Revises: 0051
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add nullable password_hash column to org_member.
    # Existing members (those authenticating via OIDC/Keycloak) will have NULL.
    op.execute(
        "ALTER TABLE org_member ADD COLUMN IF NOT EXISTS password_hash text "
        "DEFAULT NULL"
    )


def downgrade() -> None:
    op.drop_column("org_member", "password_hash")
