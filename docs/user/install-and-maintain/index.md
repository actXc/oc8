# Install and maintain

For **administrators** who deploy, upgrade, back up, and secure oc8.

This section is **not** about using the product day to day — that lives in
[User documentation](../index.md). Integrations (M365, Google, …) live under
[User → Integrations](../integrations/index.md).

## Deploy and operate

| Topic | Guide |
|-------|-------|
| Docker Compose reference stack | [Deploy](../../DEPLOY) |
| Production caveats | [Deploy — deliberate limits](../../DEPLOY#deliberate-limits) |
| Scope and limitations | [SCOPE_AND_LIMITATIONS](../../SCOPE_AND_LIMITATIONS) |
| Backup and restore | [BACKUP_RESTORE](../../BACKUP_RESTORE) |
| Pilot backup runbook | [PILOT_BACKUP_RESTORE](../../PILOT_BACKUP_RESTORE) |
| Audit / evidence export | Audit settings in the UI + Postgres backup |

## Quick deploy

```bash
./scripts/quickstart.sh
# or:
cp .env.example .env   # set secrets — see DEPLOY.md
docker compose up -d --build
```

Operators: follow [Quickstart](../quickstart.md) for first login, model, agent.

## Upgrades

```bash
git pull
docker compose up -d --build
```

The `migrate` service applies schema changes before app containers restart.

## Monitoring

```bash
docker compose logs -f backend worker scheduler
curl -s http://localhost/health   # adjust port to your .env
```

## Security checklist

- Never expose `OC8_ENV=dev` / dev-login on a public host
- Set `OC8_ENV=prod` and configure real login before network exposure
- Rotate `OC8_JWT_SECRET`, `OC8_SECRET_KEK`, and database passwords for production
- Treat Docker socket access on backend/worker as **host root-equivalent**
- [SECURITY.md](https://github.com/oc8/oc8/blob/main/SECURITY.md)

## Related

- [User docs](../index.md)
- [UI guide](../ui/index.md)
