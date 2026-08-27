# Core Architecture

This page is for anyone working on oc8's **core** — the agent runtime, the
capa loader, the authorization layer, the database layer — rather than
writing a capa. If you're building a capa, see
[Developer → Architecture Overview](../developer/architecture-overview.md)
instead; it covers the same principle from the other side.

## The microkernel principle

oc8 is a **microkernel**: a small, generic core plus everything else as
capas. This is the load-bearing structural rule for the whole codebase.

- **Core** provides mechanisms and stable extension points only — the
  agent run loop, tool dispatch, the authorization decision point, the
  approval workflow, the capa loader and contribution registry,
  connections, secrets, multi-tenancy. Core **names no specific product
  and assumes no specific use case** — not even that a tool call carries a
  monetary value.
- **Capas** carry everything specific — every integration, every
  business scenario, every guardrail preset tuned to a real workflow.

**The test for every change:** *is this a generic mechanism, or is it
specific to a piece of software or a use case?* Mechanism → core, as a
neutral seam. Specific → a capa. After touching a core module, grep it
for product/field/locale tokens — a core file that mentions a specific
vendor's field name is a sign the change belongs in a capa instead.

**Role-model implementations:** Jenkins (core + plugins), n8n (core +
nodes), WordPress (core + plugins/themes), Grafana (core + plugins/data
sources/panels), VSCode (core + extensions). Each keeps a small, stable
core and pushes all specialisation into a rich plugin ecosystem.

Two real seams that show what "mechanism, not specifics" looks like in
practice:

**`agent/tool_semantics.py`** decides where a tool call's monetary value
lives and how to describe the record it touched, without knowing anything
about the systems whose tools it inspects — it interprets a capa-
supplied declarative spec (`value_spec`/`focus_spec`), arbitrary keys and
all.

**`agent/outward.py`** enforces "at most one outward message per record
per task" without knowing what an outward message even is for any given
integration — a connection declares which of its tools count
(`outward_tools` in its `tool_pack.toml`), and an empty declaration leaves
the guard inert.

## Keep it Simple

A companion principle: the simplest thing that actually holds is the
right thing. Where the microkernel principle says *where* code belongs,
this says *how much of it there should be.* Solve the problem in front of
you, not the one you can imagine — a mechanism built for a case nobody has
yet is a cost paid every day for a benefit that may never arrive, and it's
the harder thing to delete once it's load-bearing. Prefer a fact over a
guess: a unique constraint decides a race, a check-then-write hopes; a
recorded number is a fact, a default is something someone made up.

## Subsystem map

`backend/src/oc8/`, grouped by role. Each package's own top-level
docstring is the authoritative one-line description — read it before
assuming you know what a package does from its name.

**Execution core**
- `agent/` — the execution loop, MCP tool client, and context assembly
- `runtime/` — the durable run state machine: repository, queue, executor,
  worker
- `authz/` — the Policy Decision Point that authorizes every tool call
- `modelrouter/` — the LLM-agnostic layer; switching an agent's model is a
  config change, never a data migration
- `sandbox/` — isolated Docker workspaces for agent runs
- `coding/` — the sandbox-backed agentic coding toolset

**Capa machinery**
- `capas/` — discovery, loading, the manifest schema, and the
  contribution registry (`discovery.py`, `loader.py`, `manifest.py`,
  `contributions.py`)
- `channels/` — the approval-channel seam; core owns the record, a capa
  owns which messenger it goes through

**Governance**
- `audit/` — the immutable, per-tenant hash-chained audit trail
- `evidence/` — run evidence, archived and chained into the ledger
- `approvals/` — human decisions on a held agent action
- `roles/` — tenant-defined human roles
- `metering/` — idempotent, append-only token-usage records

**Data & infrastructure**
- `db/` — the database layer: engine, base metadata, RLS-bound sessions
- `models/` — ORM models (importing this package registers every table)
- `schemas/` — pydantic response DTOs, mirroring the frontend's TypeScript
  interfaces field-for-field via camelCase
- `secrets/`, `crypto/` — the secret store and its envelope encryption
- `realtime/` — the per-tenant event fan-out gateway (Redis Pub/Sub)

**Identity & tenancy**
- `auth/` — principals and identity providers
- `tenants/`, `oauth/` — tenant lifecycle and the OAuth provisioning
  machinery capa setup forms hook into

**Business-process seams (deliberately neutral)**
- `departments/`, `automation/`, `triggers/` (cron + webhook subscriptions),
  `collab/` (cross-department handoffs/contracts/flows), `workspace/`,
  `hooks/`, `skills/`, `knowledge/`, `notifications/`, `events/`

**Operations**
- `observability/` — OpenTelemetry, fully inert when disabled
- `backup/` — tenant data export/restore
- `api/` — the thin HTTP layer

## The agent execution loop

One sentence, `agent/engine.py`'s own docstring: **trigger → assemble
context → ask the model (via the Model Router) → for each tool call:
authorize → invoke the tool → feed the result back → repeat until the
model stops.** Every tool call is audited; token usage is metered; a
guardrail threshold breach raises a human-in-the-loop approval and
suspends the run.

The MCP tool gateway — how a tool call actually reaches a capa's
connection, whether that's an in-process call or a subprocess bridge — is
`api/mcp_gateway.py`, `agent/mcp_pool.py` (the live-session cache, keyed on
`connection_id` alone — not on the launch command/args), and
`agent/mcp_client.py`.

## Multi-tenancy

Every tenant-scoped table is protected by Postgres row-level security,
driven by one transaction-local setting: `tenant_session(tenant_id)`
issues a single `set_config('app.tenant_id', :tid, true)` per transaction,
and RLS policies read `current_setting('app.tenant_id', true)`. Tables
default to **fail-closed** — an unbound session gets no rows, not
everything. There is one deliberate, documented exception (the
`organization` table, used only by the trigger scheduler to discover which
tenants have due triggers); every other tenant-scoped table stays
fail-closed when unbound. This is the entire isolation mechanism — there
is no separate per-table `tenant_id` filter scattered through application
code.

## Coding on this

- [Coding Guidelines](coding-guidelines.md) — lint/type-check configuration
  and the testing discipline this codebase expects.
- [Git Guidelines](git-guidelines.md) — commit and branch conventions.
