"""Re-sign audit_chain_checkpoint.state_mac in the v2 message format (§12.5)

0030's marker signed `tenant|max_mac_version|max_seen_seq|verified_count`.
That message was too small in two ways:

* it omitted any quantity a later append could not restore, so a wholesale
  downgrade followed by ONE honest keyed append read exactly like a normal
  migration (0,0,...,0,1) and a full re-verification returned the tenant to
  "ok"; and
* it omitted last_seq/last_hash, the incremental walk's resumption point, so
  the checkpoint could be pointed at a rewritten tail with every signed field
  left untouched.

The v2 message adds the keyed-row count below the mark (derived live, never
stored -- see integrity._state_mac) and the resumption pair. Every marker
written by 0030 is therefore invalid under v2 and must be re-signed here.

This migration also ADOPTS: on a deployment where the MAC is enabled, any
checkpoint that has verified something and yet has no marker is signed at its
current state. That is the one-time operator event the new erasure check
cannot otherwise be told apart from an attacker deleting the marker -- the two
states are information-theoretically identical, so the acknowledgement has to
happen out of band, at upgrade time, where an attacker with UPDATE on the
checkpoint table has no say.

Clearing the markers instead was the obvious alternative and is wrong: a NULL
marker over a non-fresh checkpoint on a keyed deployment is exactly the
erasure signature, so clearing would either report every tenant as broken on
the first tick after upgrade (if the check fires) or re-open the erasure hole
(if it does not).

If no usable KEK is configured, nothing is touched: the deployment is not
keyed, there is no key to sign with, and destroying markers it may want later
would be strictly worse than leaving them for the next keyed run to reject.

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-21
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

# The keyed-row count below the mark, exactly as integrity._keyed_count derives
# it: rows at or below max_seen_seq that still carry the remembered version.
_SELECT = """
SELECT c.tenant_id, c.max_mac_version, c.max_seen_seq, c.verified_count,
       c.last_seq, c.last_hash, c.state_mac,
       (SELECT count(*) FROM audit_event e
         WHERE e.tenant_id = c.tenant_id
           AND e.seq <= c.max_seen_seq
           AND e.mac_version >= c.max_mac_version) AS keyed_count
  FROM audit_chain_checkpoint c
"""


def upgrade() -> None:
    from oc8.config import get_settings
    from oc8.secrets.keyprovider import SecretError, audit_mac_key

    try:
        key = audit_mac_key()
    except SecretError:
        logger.info("0031: no usable KEK; leaving audit_chain_checkpoint.state_mac untouched")
        return
    adopt = get_settings().audit_mac_enabled

    conn = op.get_bind()
    for row in conn.execute(sa.text(_SELECT)).mappings().all():
        # Re-sign what 0030 signed; adopt an unsigned checkpoint only on a
        # deployment that is actually keyed and only when it has verified
        # something (a fresh checkpoint signs itself on its first clean pass).
        if row["state_mac"] is None and not (adopt and row["verified_count"] > 0):
            continue
        msg = (
            f"{row['tenant_id']}|{row['max_mac_version']}|{row['max_seen_seq']}"
            f"|{row['verified_count']}|{row['keyed_count']}|{row['last_seq']}"
            f"|{bytes(row['last_hash']).hex()}"
        ).encode()
        conn.execute(
            sa.text("UPDATE audit_chain_checkpoint SET state_mac = :m WHERE tenant_id = :t"),
            {"m": hmac.new(key, msg, hashlib.sha256).digest(), "t": row["tenant_id"]},
        )


def downgrade() -> None:
    # The v1 message cannot be reconstructed for adopted rows, and a v2 marker
    # read by 0030-era code fails to authenticate (reported as mac_downgrade).
    # Clearing is the only coherent downgrade: 0030-era code treats a NULL
    # marker as "not yet keyed" and re-signs on the next clean pass.
    #
    # ROUND-TRIP HAZARD, accepted deliberately. Under CURRENT code a NULL marker
    # on a checkpoint with verified_count > 0 is the ERASURE signature, not "not
    # yet keyed" -- so an upgrade -> downgrade -> upgrade cycle on a keyed
    # deployment reports every tenant broken/mac_downgrade, unclearable in band.
    # That is the same state a fresh rollout lands in (migrations ship, the flag
    # goes on afterwards), and it has the same in-band remedy: run
    # `oc8 audit-adopt-checkpoints` after re-upgrading. Preserving the markers
    # instead is not an option -- a v2 message cannot be downgraded to v1 -- and
    # leaving forward-signed markers in place would be worse: they authenticate
    # under neither regime and cannot be distinguished from a rewrite.
    op.execute("UPDATE audit_chain_checkpoint SET state_mac = NULL")
