# Governance and approvals

oc8 is built so **consequential actions can pause for a human** before they
execute. This page explains how autonomy, thresholds, and audit fit together.

## Layers of control

```text
Capa guardrail presets (defaults per tool category)
        ↓
Department frame (tool policies for the team)
        ↓
Agent settings (narrowing, autonomy level)
        ↓
Policy engine (authorizes every tool call at runtime)
        ↓
Approval inbox / messenger (human decision when required)
```

Every external tool call goes through the **MCP gateway**. The agent never
calls Odoo, email, or Slack directly.

In the UI, the inbox is **[My work](ui/my-work.md)** — there is no separate
Approvals menu.

## Autonomy levels

Agents have a configured autonomy boundary — how far they may proceed without
asking. Exact labels depend on your UI version; conceptually:

| Level | Behaviour |
|-------|-----------|
| Supervised | Most writes and sends require approval |
| Balanced | Low-risk reads auto; writes/sends over threshold ask |
| Autonomous | Only always-ask rules and high thresholds block |

Start **supervised** for new agents and integrations; loosen after you trust
behaviour in audit.

## Value thresholds

Tool calls can carry a **monetary value** (from capa-supplied semantics). If
the value exceeds the agent's or department's threshold, the run **waits for
approval**.

Examples: invoice amount, order total, refund size. Reads typically have zero
value and pass unless on an always-ask list.

## Always-ask rules

Some actions always require approval regardless of value — e.g. delete record,
send external email, change permissions. Capas ship **guardrail presets** with
sensible defaults; you can tighten further per department or agent.

## Roles and permissions

Users authenticate via the setup wizard and local accounts (or dev-login on
localhost during evaluation). Roles (e.g. org admin, operator) control who can:

- Install and enable capas
- Approve high-risk actions
- View audit and secrets configuration
- Manage departments and agents

Configure roles in **[Access & roles](ui/access-and-roles.md)** and assign people
under **[Users](ui/users.md)**.

## Approval channels

Default: **[My work](ui/my-work.md)** in the app (also header badge).

Optional capas deliver approvals to **Telegram** or **WhatsApp** so on-call
staff can approve without logging into the UI. The same approval record is used;
only the notification channel differs.

## Audit trail

Every governed action creates an **audit event**: who (agent / user), what
tool, arguments summary, approval id if any, timestamp. For deployments that
enable it, events may be **HMAC-chained** for tamper evidence.

Export audit data from **[Audit](ui/audit.md)** in Settings, or include Postgres
backups for full tenant records — see [Backup and restore](../BACKUP_RESTORE.md).

## Budgets

Model usage can be capped per tenant, department, or agent. When budget is
exceeded, new runs may be blocked or require admin override — configure in
settings according to your deployment.

## Security reminders

- **Secrets** live in an encrypted store; agents and the Copilot do not read raw
  credentials.
- **Capas are executable code** — install only from sources you trust.
- **Dev-login** must never be exposed on a public host; set `OC8_ENV=prod` and
  configure real login before sharing a network URL — see [DEPLOY](../DEPLOY.md).

## Related

- [My work](ui/my-work.md)
- [How work moves](ui/how-work-moves.md)
- [Access & roles](ui/access-and-roles.md)
- [Developer: guardrails](../developer/guardrails-and-permissions.md)
- [Scope and limitations](../SCOPE_AND_LIMITATIONS.md)
