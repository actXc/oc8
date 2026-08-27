"""a knowledge chunk learns where it came from and how it died

Today a customer can put a document into a knowledge base and can never take it
out: `kb_chunk` has no deleted_at, so retrieval has nothing to filter on, and it
has no data_source_id, so nothing can say which source a chunk came from. The
five columns here are what "erase this document" and "that document is gone
upstream" need in order to be statements about a row rather than about
application code.

Why the row survives the deletion. §12.5.1's rule is that retention is
REDUCTION: the chunk row stays as its own tombstone and only the ability to
re-read the content is dropped. That is why `deleted_at` and `reduced_at` are
two columns and not one -- a deletion the system *inferred* from a connector
listing is tombstoned now and reduced only after a grace window, and during that
window it is restorable by one UPDATE. A single timestamp would make every
inference irreversible.

`ck_kb_chunk_reduced_is_empty` is the load-bearing one. A reduced row that still
holds retrievable text is unrepresentable in the table, so "prove the document
is gone" is `SELECT count(*) ... WHERE content LIKE '%<sentence>%'` returning 0
rather than an audit of every code path that might have forgotten to blank it.
An UPDATE that stamps `reduced_at` without emptying the row fails at the moment
of the mistake instead of a year later in a DSR response.

Why nullable is the honest default for `data_source_id`. Connector-ingested
chunks carry no source identity on the row and the only correlation available is
`ingestion_job.data_source_id` plus a created_at window, which is a guess the
moment two sources feed one base -- and guessing provenance is the exact failure
this column exists to prevent. So the backfill attributes only `upload://` URIs,
where the source uuid is literally in the URI, and every other pre-0045 row
stays NULL. NULL means "provenance unknown" and is never eligible for
reconciliation: being wrong in that direction costs a chunk that outlives its
document, being wrong the other way deletes a live one. There is no follow-up
NOT NULL migration because there is no honest backfill for the rest; those rows
are attributed at runtime, by an attested listing that names them.

`last_attested_sync_at` is a real timestamptz, unlike the existing
`data_source.last_sync_at`, which is Text holding an isoformat string. It has to
be, because the whole point of it is the SQL comparison
`last_attested_sync_at >= kb_chunk.missing_since + <confirm window>`. Whoever
adds the next sync timestamp should copy this column and not that one.

Revision ID: 0045
Revises: 0044
Create Date: 2026-08-01
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_REASONS = "'operator_delete','source_absent','superseded'"

# 'upload://' is nine characters, so the uuid starts at position 10. The regex
# guard is not decoration: connectors/upload.py emits `upload://{filename}` with
# no uuid at all, and one such row must not abort the whole upgrade on a cast
# error.
_BACKFILL = """
UPDATE kb_chunk
   SET data_source_id = substring(source_uri from 10 for 36)::uuid
 WHERE data_source_id IS NULL
   AND source_uri LIKE 'upload://%'
   AND substring(source_uri from 10 for 36) ~
       '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
"""


def upgrade() -> None:
    # All three tables are in 0001's create_all frozen set (cf. 0021, 0022,
    # 0034, 0037): a database built fresh from the LIVE ORM models already has
    # these columns and constraints by the time this migration runs. IF NOT
    # EXISTS, and DROP-then-ADD for the constraints, converge the fresh-install
    # path with the incremental-deploy path.
    op.execute("ALTER TABLE kb_chunk ADD COLUMN IF NOT EXISTS data_source_id uuid")
    op.execute("ALTER TABLE kb_chunk ADD COLUMN IF NOT EXISTS deleted_at timestamptz")
    op.execute("ALTER TABLE kb_chunk ADD COLUMN IF NOT EXISTS deleted_reason text")
    op.execute("ALTER TABLE kb_chunk ADD COLUMN IF NOT EXISTS reduced_at timestamptz")
    op.execute("ALTER TABLE kb_chunk ADD COLUMN IF NOT EXISTS missing_since timestamptz")

    op.execute("ALTER TABLE data_source ADD COLUMN IF NOT EXISTS deleted_at timestamptz")
    op.execute("ALTER TABLE data_source ADD COLUMN IF NOT EXISTS last_attested_sync_at timestamptz")
    op.execute(
        "ALTER TABLE data_source ADD COLUMN IF NOT EXISTS reconcile_state text "
        "NOT NULL DEFAULT 'ok'"
    )
    op.execute("ALTER TABLE data_source ADD COLUMN IF NOT EXISTS reconcile_note text")
    op.execute("ALTER TABLE data_source DROP CONSTRAINT IF EXISTS ck_data_source_reconcile_state")
    op.execute(
        "ALTER TABLE data_source ADD CONSTRAINT ck_data_source_reconcile_state "
        "CHECK (reconcile_state = ANY (ARRAY['ok','held']))"
    )

    op.execute("ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS deleted_at timestamptz")

    result = op.get_bind().execute(sa.text(_BACKFILL))
    # Say the number out loud. If the migration role turns out to be subject to
    # RLS the UPDATE silently touches nothing -- fail-safe, but a 0 that means
    # "not permitted" must not be read as a 0 that means "nothing to do".
    logger.info("0045: attributed %s upload:// chunks to their data source", result.rowcount)

    # After the backfill, though they would hold either way: no row has
    # deleted_at yet, so both are trivially true at this point.
    op.execute("ALTER TABLE kb_chunk DROP CONSTRAINT IF EXISTS ck_kb_chunk_deleted_reason")
    op.execute(
        "ALTER TABLE kb_chunk ADD CONSTRAINT ck_kb_chunk_deleted_reason CHECK ("
        "(deleted_at IS NULL AND deleted_reason IS NULL) OR "
        f"(deleted_at IS NOT NULL AND deleted_reason = ANY (ARRAY[{_REASONS}])))"
    )
    op.execute("ALTER TABLE kb_chunk DROP CONSTRAINT IF EXISTS ck_kb_chunk_reduced_is_empty")
    op.execute(
        "ALTER TABLE kb_chunk ADD CONSTRAINT ck_kb_chunk_reduced_is_empty CHECK ("
        "reduced_at IS NULL OR "
        "(deleted_at IS NOT NULL AND content = '' AND embedding IS NULL))"
    )

    # All four partial, in 0043's style: the rows they exclude are the
    # overwhelming majority of the widest table in the schema, and kb_chunk is
    # written a chunk at a time by every ingest.
    #
    # Document identity, and the reconciler's baseline DISTINCT. data_source_id
    # IS NULL is a usable btree condition, which is what makes orphan adoption
    # an index scan rather than a seq scan.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kb_chunk_document ON kb_chunk "
        "(tenant_id, data_source_id, kb_id, source_uri) WHERE deleted_at IS NULL"
    )
    # The sweep's mark -> kill work list.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kb_chunk_missing ON kb_chunk (tenant_id, missing_since) "
        "WHERE missing_since IS NOT NULL AND deleted_at IS NULL"
    )
    # The write-path suppression set, read once per sync. Small by construction:
    # only an operator's own deletions land in it.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kb_chunk_suppressed "
        "ON kb_chunk (tenant_id, kb_id, source_uri) "
        "WHERE deleted_at IS NOT NULL AND deleted_reason = 'operator_delete'"
    )
    # The reduce work list. Unlike the other three this one drains.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kb_chunk_unreduced ON kb_chunk (tenant_id, deleted_at) "
        "WHERE deleted_at IS NOT NULL AND reduced_at IS NULL"
    )


def downgrade() -> None:
    """Lossy by definition, and said out loud rather than left to look reversible.

    Dropping `deleted_at` does not restore a deleted document -- it makes every
    tombstone answerable again, including the ones an operator erased under a
    GDPR request, and every reduced tombstone comes back as an empty chunk with
    no way to tell it from a real one. Downgrading past 0045 after anything has
    been deleted is a data incident, not a rollback.
    """
    op.execute("DROP INDEX IF EXISTS ix_kb_chunk_unreduced")
    op.execute("DROP INDEX IF EXISTS ix_kb_chunk_suppressed")
    op.execute("DROP INDEX IF EXISTS ix_kb_chunk_missing")
    op.execute("DROP INDEX IF EXISTS ix_kb_chunk_document")
    op.execute("ALTER TABLE kb_chunk DROP CONSTRAINT IF EXISTS ck_kb_chunk_reduced_is_empty")
    op.execute("ALTER TABLE kb_chunk DROP CONSTRAINT IF EXISTS ck_kb_chunk_deleted_reason")
    op.execute("ALTER TABLE knowledge_base DROP COLUMN IF EXISTS deleted_at")
    op.execute("ALTER TABLE data_source DROP CONSTRAINT IF EXISTS ck_data_source_reconcile_state")
    op.execute("ALTER TABLE data_source DROP COLUMN IF EXISTS reconcile_note")
    op.execute("ALTER TABLE data_source DROP COLUMN IF EXISTS reconcile_state")
    op.execute("ALTER TABLE data_source DROP COLUMN IF EXISTS last_attested_sync_at")
    op.execute("ALTER TABLE data_source DROP COLUMN IF EXISTS deleted_at")
    op.execute("ALTER TABLE kb_chunk DROP COLUMN IF EXISTS missing_since")
    op.execute("ALTER TABLE kb_chunk DROP COLUMN IF EXISTS reduced_at")
    op.execute("ALTER TABLE kb_chunk DROP COLUMN IF EXISTS deleted_reason")
    op.execute("ALTER TABLE kb_chunk DROP COLUMN IF EXISTS deleted_at")
    op.execute("ALTER TABLE kb_chunk DROP COLUMN IF EXISTS data_source_id")
