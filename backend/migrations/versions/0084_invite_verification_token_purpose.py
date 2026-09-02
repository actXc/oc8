"""account_verification_token gains purpose='invite' -- an administrator who
creates a member with no password (`POST /members`) now mints a link of this
purpose, mailed (or handed back for manual sharing) so that person can set
their own password instead of being left with no way in at all. Redeemed by
the SAME endpoint as a `password_reset` link (`POST /auth/password/reset`,
via `_redeem_token(purpose=("password_reset", "invite"))`) -- both purposes
authorize the identical action, "set a new password", so they share one
redemption door rather than needing a second endpoint.

Revision ID: 0084
Revises: 0083
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0084"
down_revision: str | None = "0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_account_verification_token_purpose", "account_verification_token", type_="check"
    )
    op.create_check_constraint(
        "ck_account_verification_token_purpose",
        "account_verification_token",
        "purpose IN ('password_reset','email_change','invite')",
    )


def downgrade() -> None:
    # Any live 'invite' row would violate the narrower constraint below, so it
    # is removed rather than reassigned -- unlike 0073's downgrade, which
    # remaps 'webhook' agent_run rows to a fallback value that keeps meaning
    # ('event') because the two runtime behaviors are close cousins. There is
    # no such fallback purpose here: 'password_reset' and 'email_change' are
    # both wrong facts about how an 'invite' row came to exist, and a stale or
    # spent link (this table's whole population, days after mint) is safe to
    # delete outright -- downgrading past this migration means the invite
    # feature no longer exists, so a link nothing can redeem any more is not a
    # loss.
    op.execute("DELETE FROM account_verification_token WHERE purpose = 'invite'")
    op.drop_constraint(
        "ck_account_verification_token_purpose", "account_verification_token", type_="check"
    )
    op.create_check_constraint(
        "ck_account_verification_token_purpose",
        "account_verification_token",
        "purpose IN ('password_reset','email_change')",
    )
