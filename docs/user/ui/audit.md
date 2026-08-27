# Audit

**Route:** `/audit` · **Settings** section

## What it is

The **compliance archive**: a filterable, optionally integrity-checked chain of
governed events (who did what, when, under whose approval).

## What it is for

- Prove what happened after the fact
- Export evidence
- Detect tampering when HMAC chaining is enabled

## Where you are in the flow

```text
Day-to-day ops → Activity (live) · ★ Audit (evidence / compliance)
```

Use [Activity](activity.md) to debug today. Use Audit when someone asks “show me
the record” weeks later.

## What you do here

1. Open **Audit**; check integrity banner if shown.
2. Filter the event chain.
3. Export from the UI or include audit data in your [Postgres backup](../../BACKUP_RESTORE).

## Where work goes next

| Goal | Next |
|------|------|
| Live debugging | [Activity](activity.md) |
| Backup of the database | [Install → Backup](../install-and-maintain/index.md) |

## Related

- [Governance and approvals](../governance-and-approvals.md)
- [Backup and restore](../../BACKUP_RESTORE)
