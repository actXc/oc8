# What is oc8?

oc8 is an **open-source AI agent management platform** you run on infrastructure
you control. It turns recurring work — triage tickets, update a CRM, draft
replies, reconcile records — into **configured agents** whose actions you can
**approve, limit, and audit**.

oc8 is **not** a chatbot. Agents here **do work**: they call tools, write to
systems, and wait for human approval when a step crosses a boundary you set.

## The problem oc8 solves

| Without oc8 | With oc8 |
|-------------|----------|
| Ad-hoc scripts and API keys in notebooks | Agents with a defined role, tools, and budget |
| "The AI did something" with no trail | Audit log: who approved what, when |
| Every integration is custom glue code | **Capas** — drop-in extensions for tools and scenarios |
| Credentials in prompts or env files | Encrypted secret store; agents never see raw keys |

## How work flows through oc8

```text
You describe an outcome
        ↓
An agent (in a department) plans steps
        ↓
Each tool call is checked against permissions
        ↓
Over-threshold or sensitive actions → approval inbox (or Telegram/WhatsApp)
        ↓
Approved actions execute; everything is logged
```

## What you configure

| Piece | You decide… |
|-------|-------------|
| **Department** | Which tools and knowledge bases agents in this team may use (the *frame*) |
| **Agent** | Model, mission, skills, and how autonomous it may be |
| **Capas** | Which extensions are installed (Odoo MCP, M365, agent templates, …) |
| **Governance** | Value thresholds, always-ask lists, roles, budgets |

## What stays in the core

The **microkernel** handles mechanisms only: run queue, tool gateway, approval
workflow, multi-tenancy, secrets. Anything specific to a vendor (Odoo fields,
Teams channels, a particular agent persona) lives in a **capa**, not in core.

## Self-hosted by design

There is no oc8.cloud account. You run Postgres, Redis, the API, worker, and
frontend — typically via Docker Compose. Your data and credentials stay on your
network.

## Next steps

- [Key concepts](key-concepts.mdx) — vocabulary you'll see in the UI
- [Getting started](getting-started.md) — stand up a instance and run one task
- [Scope and limitations](../SCOPE_AND_LIMITATIONS.md) — what is / isn't production-ready yet
