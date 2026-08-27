"""audit_chain_checkpoint.max_mac_version + state_mac (§12.5 keyed chain)

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # audit_chain_checkpoint was created by a normal op.create_table in 0025,
    # not by 0001's frozen snapshot, so plain add_column is right here --
    # unlike audit_event, which needs raw DDL (see 0029).
    op.add_column(
        "audit_chain_checkpoint",
        sa.Column("max_mac_version", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.add_column("audit_chain_checkpoint", sa.Column("state_mac", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_chain_checkpoint", "state_mac")
    op.drop_column("audit_chain_checkpoint", "max_mac_version")
