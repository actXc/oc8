# Pilot Backup and Restore Runbook

This is the minimum recovery procedure for the Docker Compose pilot stack. It
backs up the PostgreSQL database, restores it into a separate verification
database, and proves that the restore contains the expected tenant data. Redis
is intentionally excluded: it holds queues and realtime fan-out state, not the
system of record. A restored stack starts with empty queues.

Run this once before inviting pilot users and record the date, operator, backup
file checksum, restore duration, and result in the pilot log. Repeat after a
schema migration and at least monthly during the pilot.

## Preconditions

- The root Compose stack is running (`docker compose ps`).
- The host has enough free disk space for a complete SQL dump.
- `.env` is present; its `POSTGRES_PASSWORD` is required by the commands.
- Keep the backup outside the repository and encrypt it at rest. A SQL dump
  contains every tenant's data and audit history.

## Create a backup

```bash
mkdir -p backups
backup_file="backups/oc8-$(date +%Y%m%d-%H%M%S).sql.gz"
docker compose exec -T postgres pg_dump -U postgres -d oc8 | gzip > "$backup_file"
shasum -a 256 "$backup_file" > "$backup_file.sha256"
gzip -t "$backup_file"
```

Copy both files to the pilot's approved encrypted backup location. The SHA-256
file lets a later operator reject a corrupted or incomplete transfer before
attempting recovery.

## Rehearse a restore safely

The verification database is separate from the live `oc8` database. This does
not stop or modify the application stack.

```bash
backup_file="backups/oc8-YYYYMMDD-HHMMSS.sql.gz"

docker compose exec -T postgres psql -U postgres -d postgres \
  -c 'DROP DATABASE IF EXISTS oc8_restore_verify WITH (FORCE);'
docker compose exec -T postgres psql -U postgres -d postgres \
  -c 'CREATE DATABASE oc8_restore_verify;'
gzip -dc "$backup_file" | docker compose exec -T postgres psql -U postgres -d oc8_restore_verify

# Proof that the structural tenant and application data survived.
docker compose exec -T postgres psql -U postgres -d oc8_restore_verify -c \
  'SELECT slug, name, tier, region FROM organization ORDER BY slug;'
docker compose exec -T postgres psql -U postgres -d oc8_restore_verify -c \
  'SELECT COUNT(*) AS audit_events FROM audit_event;'
```

Record the output (with no secrets), restore duration, and whether the expected
tenant(s) appear. A successful restore is not merely a command exiting zero:
the `organization` rows and expected application data (for example, agents) are
the proof. A fresh seeded stack may legitimately have no audit events yet.

Remove the verification database after recording the result:

```bash
docker compose exec -T postgres psql -U postgres -d postgres \
  -c 'DROP DATABASE oc8_restore_verify WITH (FORCE);'
```

## Recovery of the live pilot

This is a deliberate destructive operation. Stop here unless the pilot owner
has authorized replacing the current database with the selected backup.

1. Stop application writers: `docker compose stop backend worker ingestion-worker scheduler`.
2. Take one final dump of the damaged state using the backup command above.
3. Drop and recreate `oc8` inside Postgres, restore the selected dump, then run
   `docker compose up -d backend worker ingestion-worker scheduler`.
4. Confirm `/health`, login, tenant list, one known agent, and audit integrity
   (`GET /api/v1/audit/integrity`) before allowing users back in.

Do not run `docker compose down -v` for recovery: it deletes the volume that
contains the very data being recovered.
