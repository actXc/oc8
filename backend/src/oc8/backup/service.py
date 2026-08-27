"""Restoring a company from an archive (design doc §5).

The order of operations IS the design, and it runs in one caller-owned
transaction (see `restore_from_archive`'s docstring): validate, decrypt any
secrets BEFORE anything is deleted, delete every in-scope table's rows for
the target tenant in reverse dependency order, insert the archive's rows in
forward dependency order with `tenant_id` rewritten, update the
organization's display fields, re-store the decrypted secrets, and append
one audit event.

Never commits: the Postgres RLS GUC (`app.tenant_id`) is transaction-local,
so a mid-service commit would unbind it for every statement after -- in the
one function that deletes whole tables. This module only `execute`s and
`flush`es, matching the rule already stated in `oc8.secrets.service`; the
caller (the API endpoint) owns the transaction boundary.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.audit import append_event
from oc8.auth.principal import Principal
from oc8.backup.archive import current_alembic_revision, excluded_for_manifest
from oc8.backup.errors import BackupError
from oc8.backup.reader import parse_archive
from oc8.backup.secrets_envelope import decrypt_secrets
from oc8.backup.serialization import deserialize_row
from oc8.backup.tables import dependency_order, exported_tables, table_by_name
from oc8.secrets.service import store_secret


@dataclass
class RestorePreview:
    manifest: dict[str, Any]
    table_counts: dict[str, dict[str, int]]
    has_secrets: bool
    problems: list[str]


async def preview_restore(
    db: AsyncSession, *, tenant_id: uuid.UUID, archive_bytes: bytes
) -> RestorePreview:
    """Read-only look at what a restore would do: per-table archive vs.
    current row counts, whether the archive carries secrets, and every
    problem `parse_archive` can report without raising (`strict=False`).
    Writes nothing."""
    current_revision = await current_alembic_revision(db)
    parsed, problems = parse_archive(archive_bytes, current_revision=current_revision, strict=False)
    counts: dict[str, dict[str, int]] = {}
    for name in exported_tables():
        table = table_by_name(name)
        current = (
            await db.execute(
                select(func.count()).select_from(table).where(table.c.tenant_id == tenant_id)
            )
        ).scalar_one()
        archive_count = len(parsed.tables.get(name, []))
        if archive_count or current:
            counts[name] = {"archive": archive_count, "current": current}
    return RestorePreview(
        manifest=parsed.manifest,
        table_counts=counts,
        has_secrets=parsed.secrets_ciphertext is not None,
        problems=problems,
    )


@dataclass
class RestoreResult:
    tables: dict[str, int]
    secrets_restored: int
    excluded: list[str]


async def restore_from_archive(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    archive_bytes: bytes,
    passphrase: str | None,
    actor_type: str,
    actor_id: uuid.UUID | None,
    principal: Principal | None = None,
) -> RestoreResult:
    """Replace `tenant_id`'s data with the archive's, in one transaction.
    Never commits -- see the module docstring. Raises `BackupError` (or a
    subclass, e.g. `WrongPassphrase`) on any problem, and raises before any
    row has been touched wherever that is possible (validation, passphrase)."""
    current_revision = await current_alembic_revision(db)
    parsed, _ = parse_archive(archive_bytes, current_revision=current_revision, strict=True)

    # Decrypt BEFORE any delete (design doc §8): a typo'd passphrase must
    # fail loudly with the company still completely intact, not after it has
    # already been erased.
    decrypted_secrets: dict[str, str] = {}
    if parsed.secrets_ciphertext is not None:
        if not passphrase:
            raise BackupError("archive contains secrets.json but no passphrase was given")
        decrypted_secrets = decrypt_secrets(
            parsed.secrets_ciphertext, passphrase, parsed.manifest["secrets"]
        )

    tables = exported_tables()
    order = await dependency_order(db, tables)

    # DELETE every in-scope table's rows for this tenant, parents last (reverse
    # dependency order): a child row referencing a not-yet-deleted parent is
    # fine, but the delete must never leave a dangling FK the other way round.
    # No table needs skipping here: everything migration 0001 marks immutable
    # is already out of `exported_tables()` (see EXCLUDED_TABLES), because a
    # table the app may not DELETE cannot be replaced, only merged into.
    for name in reversed(order):
        table = table_by_name(name)
        await db.execute(delete(table).where(table.c.tenant_id == tenant_id))

    # INSERT the archive's rows, parents first (forward dependency order),
    # with tenant_id rewritten to the target and every id carried over
    # verbatim -- see the module docstring: every id is a UUID, so an archive
    # from elsewhere cannot collide, and the delete above means an archive of
    # THIS company cannot collide with its own older rows either.
    rows_written: dict[str, int] = {}
    for name in order:
        table = table_by_name(name)
        payload = []
        for raw in parsed.tables.get(name, []):
            row = deserialize_row(table, raw)
            row["tenant_id"] = tenant_id
            if name == "agent_run":
                # A restored run's evidence folder does not exist on this
                # instance (design doc §2.3: evidence is a blob, never a
                # row) -- force the bookkeeping to match reality rather than
                # carrying over a stale 'present'/'archived'/'reduced'.
                row["evidence_state"] = "none"
            payload.append(row)
        if payload:
            await db.execute(insert(table), payload)
        rows_written[name] = len(payload)

    # This instance's own identity and licensing are not the archive's to
    # set -- only the display fields move.
    organization = await db.get(m.Organization, tenant_id)
    if organization is None:
        raise BackupError(f"no organization found for tenant {tenant_id}")
    organization.name = parsed.manifest["source"]["name"]
    organization.region = parsed.manifest["source"]["region"]

    # Re-encrypt each decrypted secret under the TARGET tenant's own DEK.
    # An archive with no secrets leaves this loop empty, and `secret`/
    # `tenant_dek` were never touched by the deletes above (they are not in
    # `exported_tables()`) -- so the target's existing secrets survive
    # untouched exactly when the archive carries none.
    secrets_restored = 0
    for name, value in decrypted_secrets.items():
        await store_secret(db, tenant_id=tenant_id, name=name, value=value)
        secrets_restored += 1

    await append_event(
        db,
        tenant_id=tenant_id,
        actor_type=actor_type,
        actor_id=actor_id,
        category="backup",
        action="restore",
        # Without this, `resolve_responsible` cannot see a human here and
        # files the event under the tenant -- so the audit log for the single
        # most destructive action in the product would not name who did it.
        principal=principal,
        resource={
            "source_tenant_id": parsed.manifest["source"]["tenant_id"],
            "source_slug": parsed.manifest["source"]["slug"],
            "source_created_at": parsed.manifest["created_at"],
            "tables": rows_written,
            "secrets_restored": secrets_restored,
        },
    )

    # Same archive-aware list the manifest itself carries (design doc §2.2,
    # §2.3, §9's "the restore must be honest about what it did not restore"):
    # `secret`/`tenant_dek` drop out of it exactly when THIS archive carried
    # `secrets.json` -- reaching this line with `parsed.secrets_ciphertext is
    # not None` means `decrypt_secrets` above already succeeded (a bad
    # passphrase raises before any of this runs), so the credentials really
    # did travel and it would be self-contradictory to report them as "not
    # restored" beside a `secrets_restored` count above zero.
    excluded = excluded_for_manifest(secrets_included=parsed.secrets_ciphertext is not None)
    return RestoreResult(tables=rows_written, secrets_restored=secrets_restored, excluded=excluded)
