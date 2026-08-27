# Scope and limitations

oc8 is **under active development**. This page sets expectations before you
depend on it for production workloads.

## What is stable enough to try

- Self-hosted Docker Compose stack (Postgres, Redis, API, workers, frontend)
- Agent / department / task / run model
- MCP tool gateway with authorization and approvals
- Capa install, enable, and permission consent
- Encrypted secret store
- Knowledge ingestion and memory (with configuration)
- Audit logging (optional HMAC chain)
- Pilot backup/restore runbook for Postgres

See [ROADMAP.md](https://github.com/oc8/oc8/blob/main/ROADMAP.md) for shipped vs planned features.

## Known limitations (reference deployment)

These apply to the default `docker-compose.yml` stack unless you harden it:

| Area | Limitation |
|------|------------|
| **Authentication** | `OC8_ENV=dev` enables unauthenticated **dev-login** — anyone who can reach the API gets admin. Use network isolation or `OC8_ENV=prod` before exposing a host. |
| **TLS** | HTTP by default; terminate TLS at your reverse proxy or add Caddy config. |
| **High availability** | Single-node Compose; no built-in HA Postgres or Redis. |
| **Secrets** | Operator-managed `.env`; no Vault/KMS integration in the reference stack. |
| **Container runtime** | Backend/worker mount Docker socket — **root-equivalent** on the host. |
| **Sandbox** | Agent sandbox pulls images at run time; docker-out-of-docker, not full multi-tenant isolation. |
| **Observability** | OTLP export off by default; no bundled Grafana/Prometheus. |
| **OpenAPI / public API** | API v1 exists; public API documentation is still maturing. |
| **Internationalisation** | UI primarily English; docs being expanded. |

Full deployment caveats: [DEPLOY.md § Deliberate limits](DEPLOY.md#deliberate-limits).

## Pilot vs production

**Pilot** — internal team, VPN or firewall, rehearsed backups, dev-login never
network-reachable.

**Production** — requires at minimum:

1. `OC8_ENV=prod` and configured login (not dev-login)
2. Strong secrets and rotated DB credentials
3. TLS and network segmentation
4. Tested backup/restore ([BACKUP_RESTORE.md](BACKUP_RESTORE.md))
5. Reviewed capa trust boundary (only install vetted extensions)
6. Explicit decision on Docker socket exposure vs alternative runtime isolation

## Data and compliance

- You host all data; oc8 does not phone home by default.
- SQL backups contain full tenant data — encrypt at rest.
- GDPR/export: plan data export with your legal/compliance process; backups
  contain the full tenant record.

## Support model (open source)

Community support via GitHub issues and discussions. No SLA unless you contract
support separately.

## Related

- [What is oc8?](user/what-is-oc8.md)
- [Getting started](user/getting-started.md)
- [Security policy](https://github.com/oc8/oc8/blob/main/SECURITY.md)
