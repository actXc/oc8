# Backup and restore

oc8's **system of record is PostgreSQL**. Redis holds queues and ephemeral
state — it is not included in standard backups and can be empty after restore.

## Summary

1. **Back up** Postgres regularly (`pg_dump`, compressed).
2. **Store** dumps encrypted off-host with checksums.
3. **Rehearse restore** into a verification database before you need it.
4. **Recover live** only with explicit authorisation — destructive to current DB.

## Full runbook

Follow the step-by-step pilot procedure:

→ **[Pilot backup and restore runbook](PILOT_BACKUP_RESTORE.md)**

It includes:

- Preconditions and encryption expectations
- Backup commands with SHA-256 checksum
- Safe restore rehearsal into `oc8_restore_verify`
- Live recovery steps and what **not** to do (`docker compose down -v`)

## When to run backups

- Before inviting pilot users
- After every schema migration
- At least monthly during active pilot use
- Before major upgrades (`git pull && docker compose up -d --build`)

## After restore

Verify:

- `/health` returns OK
- Login works with your configured accounts
- Expected tenants and agents appear
- `GET /api/v1/audit/integrity` if HMAC audit is enabled

## Related

- [Install and maintain](user/install-and-maintain/index.md)
- [Deploy](DEPLOY.md)
