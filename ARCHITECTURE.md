# Architecture

oc8 is a microkernel: a small, domain-neutral core (agents, runs, approvals,
memory, tools, secrets) plus everything vendor- or workflow-specific loaded
in as a **capa**. The core has no idea what Odoo, Slack or a helpdesk is —
those live in `capas/`.

```
                         ┌─────────────────────────┐
                         │        caddy (edge)      │
                         └────────────┬─────────────┘
                    ┌──────────────────┴───────────────────┐
                    │                                       │
           ┌────────▼────────┐                    ┌─────────▼─────────┐
           │     frontend      │                    │      backend       │
           │  (React/Vite SPA) │◄──────REST/WS──────│  (FastAPI, API v1) │
           └────────────────────┘                    └─────────┬──────────┘
                                                                 │
                     ┌───────────────────┬───────────────────┬─┴───────────────┐
                     │                   │                   │                 │
              ┌──────▼──────┐    ┌───────▼──────┐   ┌────────▼───────┐  ┌──────▼──────┐
              │   worker      │    │  scheduler    │   │ ingestion-worker│  │  postgres    │
              │ (run executor)│    │ (cron/trigger)│   │  (RAG ingest)   │  │  + pgvector  │
              └──────┬────────┘    └──────┬────────┘   └────────┬────────┘  └─────────────┘
                     │                    │                     │
                     └───────────┬────────┴─────────────────────┘
                                 │ (Redis Streams: run queue, pub/sub)
                          ┌──────▼──────┐
                          │    redis     │
                          └──────────────┘

           ┌──────────────────────────────────────────────────────┐
           │  runtime-provisioner (sole holder of the Docker socket) │
           │  authenticated HTTP API only — nobody else touches Docker│
           └──────────────────────────┬───────────────────────────┘
                                       │ provisions
                              ┌────────▼────────┐
                              │  per-agent        │
                              │  sandbox container │  (network: internal, no
                              └────────────────────┘   route off the host)
```

## Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python, FastAPI, SQLAlchemy (async) | modular monolith, not microservices |
| Database | PostgreSQL 15 + pgvector | row-level security for isolation, pgvector for RAG |
| Queue / cache | Redis 7 (Streams) | durable run queue with consumer groups, pub/sub for realtime, department-scoped prompt-response cache |
| Frontend | React + Vite + TanStack Router | SPA |
| Sandboxing | Docker (via `docker-py`) | per-agent containers, `runtime-provisioner` service |
| Edge | Caddy 2 | reverse proxy / TLS termination |
| Auth | local password only | single-instance, built-in TOTP 2FA |
| Agent↔tool bridge | MCP (Model Context Protocol) over HTTP | oc8 itself is the only MCP server an agent ever sees |
| Migrations | Alembic | one-shot `migrate` service runs the chain, then app services start |

## Repo layout

```
backend/src/oc8/
  agent/             # the agent execution engine: tool calls, control tools, claims
  agents/, departments/ # agent + department CRUD, roles, reporting lines, guardrail frames
  api/               # FastAPI routers, incl. api/mcp_gateway.py (the tool gateway)
  approvals/         # human-in-the-loop decision service (approve/reject, any channel)
  authz/             # PDP: department frame ∩ agent narrowing, permission catalog
  auth/              # identity provider, local password auth, principal
  caching/           # department-scoped, exact-match LLM response cache (Redis-backed)
  channels/          # approval/notification channels (Telegram, WhatsApp, ...)
  db/                # engine/session; tenant_session() sets the RLS GUC per transaction
  knowledge/         # ingestion, chunking, embeddings, retrieval
  memory/            # agent/department/company memory stores
  metering/          # token/cost metering, budgets, hard stops
  modelrouter/       # provider-neutral model adapters (Anthropic, OpenAI, Ollama, ...)
  capas/             # manifest, discovery, loader, contribution registry — the microkernel seam
  runtime/            # run lifecycle: intake, queue, executor, worker, state machine
  runtime_provisioner/ # the one service with Docker socket access
  sandbox/           # SandboxDriver contract + Docker driver + orphan reaper
  secrets/           # envelope-encrypted secret store (KeyProvider + AES-256-GCM)
  supervision/       # escalation/oversight hooks for runtime composition
  tenants/           # instance/tenant provisioning (bootstrap, member/role CLI)
frontend/src/
  routes/            # TanStack Router pages (agents, departments, runs, approvals, ...)
  components/        # shared UI, incl. onboarding + guardrail preset picker
  lib/               # api.ts, hooks.ts, i18n, collaboration (WS client)
capas/               # first-party capa folders (Odoo MCP, connectors, skill packs, ...)
```

## How a run flows through the system

1. **Trigger.** A cron schedule, webhook, user chat message, or a delegating
   agent's `delegate_task` control tool calls into `runtime/intake.py`
   (`enqueue_run`) — the only place an `AgentRun` row is ever created.
2. **Enqueue.** Intake commits the `AgentRun` row first, then publishes it to
   a Redis Stream consumer group (`runtime/queue.py`). Commit-before-enqueue
   means a worker can never see a message for a run that isn't durably
   recorded yet.
3. **Claim.** A `worker` process (there are `OC8_WORKERS` replicas, each
   executing runs strictly serially) claims one stream entry via the
   consumer group and calls `execute_run`.
4. **Tenant-bind.** The executor opens a `tenant_session`, which issues
   `SELECT set_config('app.tenant_id', ...)` as the transaction's first
   statement — every subsequent query in that transaction is RLS-scoped.
5. **Authorize + resolve runtime.** The run is checked against agent status
   (e.g. an unapproved "hire-gated" agent cannot run), then
   `runtime/registry.py` resolves a `RuntimeAdapter`: in-process
   (`Oc8AgentRuntime`, the default) or, when `OC8_AGENT_ISOLATION=true`, a
   per-agent Docker container (`DockerIsolatedRuntime`) provisioned through
   the `runtime-provisioner`.
6. **Execute.** The agent loop calls the model (via `modelrouter`) and, for
   any external action, calls the MCP tool gateway — never a provider or
   external system directly.
7. **Govern each tool call.** The gateway resolves credentials control-plane
   side, checks the call against the department frame ∩ agent narrowing
   (the PDP), and — if the call exceeds an approval threshold — parks the
   run in `waiting_for_approval` instead of executing it.
8. **Resolve or complete.** A human decides the pending approval (from the
   UI or a channel like Telegram); the run resumes. Otherwise the run
   reaches a terminal state (`done`/`failed`) and its task closes with it.
9. **Realtime.** State changes are emitted over WebSocket (`realtime/emit.py`)
   so the frontend reflects the run live, without polling.

## Microkernel and capas

The core (`backend/src/oc8/`) contains no vendor- or workflow-specific logic.
A **capa** is a folder under `capas/` with a `plugin.toml` manifest
(`oc8.capas.manifest.Manifest`) declaring a `type` — `connector`,
`tool_pack`, `skill`, `department_template`, `model_adapter`,
`runtime_adapter`, `approval_channel`, `core_extension`, etc. — and a
`trust` level (`first_party` / `verified` / `community`).

- A **data-only capa** ships just declarative content (a skill's
  instructions, a tool pack's MCP connection + guardrail presets, a
  department template) — no code is imported; it "loads" to a no-op.
- A **code capa** additionally declares `entry_points` (`module:attr`);
  the loader imports that module *only if trust is `first_party` or
  `verified`* and calls `register(contrib)`, letting it contribute a
  connector, runtime adapter, or model adapter implementation. Putting a
  folder on the capas path *is* the trust decision (same model as Odoo
  addons) — running untrusted third-party code safely is out of scope until
  isolation is stronger than today's container boundary.
- A broken or untrusted capa is **quarantined** in-process (import
  attempted once, failure cached) so it costs one failure, not one per
  request; a per-tenant circuit breaker separately quarantines an
  *installation* after repeated runtime failures.

## Multi-tenancy and RLS

Every tenant-scoped table is protected by PostgreSQL row-level security
keyed on `app.tenant_id`, a **transaction-local** GUC set by
`db/session.py`'s `tenant_session()` as the first statement of every request
transaction (`SELECT set_config('app.tenant_id', :tid, true)`). RLS policies
read `current_setting('app.tenant_id', true)` and fail closed — an unbound
session sees no rows — with one narrow, read-only exception for the
scheduler/webhook path that must enumerate tenants to find due triggers.

oc8 runs as a **single-instance** product: one deployment holds exactly one
root organization, there is no tenant picker or tenant-switch UI, and users
cannot select a tenant. The RLS/tenant-id mechanism is still the enforced
isolation primitive — defense in depth, and the same shape every write path
already uses, even though a single deployment only ever binds it to one
tenant.

## Agent runtimes and the sandbox trust boundary

An agent run executes one of two ways, chosen per-agent by
`runtime/registry.py::resolve_runtime`:

- **In-process** (`Oc8AgentRuntime`, the default): the run executes inside
  the `worker` process itself.
- **Isolated** (`DockerIsolatedRuntime`, opt-in via `OC8_AGENT_ISOLATION`):
  the run executes in its own Docker container — a credential-free shell
  that drives the run over an authenticated internal HTTP API. Provider API
  keys, MCP credentials and the secret-store KEK never enter the container;
  it holds only a run-scoped token.

Either way, **only `runtime-provisioner` talks to the Docker/Podman
socket.** It's a separate service (own compose entry, read-only socket
mount) that exposes a small, token-authenticated HTTP API
(`ExecRequestDTO`/`SandboxSpecDTO`/...); the backend and worker call that
API and never construct a Docker client themselves. Agent containers run on
an `internal: true` Compose network — no route off the host — so they can
reach only the control plane's two gateways (LLM, MCP), not the database or
the open internet. An orphan `reaper` separately sweeps containers a killed
worker left running, scoped strictly to containers whose named run has
already finished, so it's safe to run unsynchronized across workers.

## The MCP tool gateway

Agents are configured with exactly one MCP server: oc8 itself
(`api/mcp_gateway.py`). The agent never learns a real downstream server
(Odoo, a CRM, a ticketing system) exists. Every tool call goes through this
gateway, which:

- resolves credentials control-plane-side (the agent never receives a raw
  provider credential where a scoped token is available),
- checks the call against the PDP (department frame ∩ agent narrowing),
- records the call for idempotency/replay safety and blast-radius tracking,
- and — the reason the gateway exists — can park a run in
  `waiting_for_approval` instead of letting an over-threshold call through.

It speaks MCP's streamable-HTTP transport (JSON-RPC over POST) because that
is the protocol an agent harness already speaks; the gateway is the seam,
not a bespoke REST shape the agent would have to learn.

## Approvals and guardrails

Autonomy is bounded, not implicit. Each department has a **frame** (a JSON
policy naming per-tool `read`/`write`/`send` rights, an `approval_eur`
value threshold, and a list of actions that always require a human); each
agent may carry a **narrowing** that can only remove rights or lower a
threshold from that frame, never widen it — enforced both when a narrowing
is written and again at decision time. Capas can ship named
**guardrail presets** (`ToolPackConnection.guardrail_presets` in the capa
manifest) as a convenience over hand-configuring a frame, including
restricting which tools within a connection are reachable at all (rights
alone are too coarse — e.g. a record deletion has no monetary value to
threshold against).

When a call crosses that ceiling, `approvals/service.py` is the single
place a decision is made, whatever channel it came in on: the inbox UI,
Telegram, WhatsApp — every channel is a *view and input device* over the
same `ApprovalRequest` row, never a parallel approval system, so RBAC, the
audit line and the resume path are identical regardless of where the
decision came from.

## The secret store

Provider API keys, MCP credentials and other tenant secrets are stored with
envelope encryption: a per-tenant **DEK** (data-encryption-key, AES-256,
generated with `AESGCM.generate_key`) encrypts the secret value
(AES-256-GCM, with `tenant_id|name` as AEAD associated data so a ciphertext
row cannot be replayed under another tenant or secret name); the DEK itself
is wrapped by a root **KEK** (key-encryption-key) read from an environment
variable (`OC8_SECRET_KEK`, base64, 256-bit) and never stored in the
database. `KeyProvider` (`secrets/keyprovider.py`) is a swappable port — the
env-backed implementation is what oc8 ships, and it fails closed: a missing
or malformed KEK raises rather than ever falling back to storing a secret in
plaintext.

Be precise about what this protects: a compromised database (a leaked
backup, a stolen disk, an unauthorized `SELECT`) yields only ciphertext —
the KEK isn't in that data. It does **not** protect against a compromised
*application process*, which holds the KEK in memory and can call
`resolve_secret` itself; that boundary is enforced by RBAC/audit around who
can reach the secret-resolving code paths, not by the encryption.

## Key Design Decisions

- **Core stays domain-neutral, capas carry the specifics** — Odoo, Slack,
  any vendor lives in a capa folder, never in `oc8/`, so the core has one
  thing to get right instead of N vendor integrations.
- **Postgres RLS, not application-level filtering, isolates rows** — a
  request that forgets a `WHERE tenant_id = ...` fails closed (sees
  nothing) rather than leaking, because the database enforces it, not
  application code.
- **Only one process ever holds the Docker socket** — `runtime-provisioner`
  is a distinct, minimally-privileged service; every other oc8 process is
  one authenticated HTTP hop away from Docker, not one API call away.
- **Agents call one MCP server, never real systems directly** — routing
  every tool call through the gateway is what makes a value-based approval
  threshold enforceable at all, instead of advisory.
- **Approval decisions live in one function regardless of channel** — a
  Telegram callback and a UI click both funnel through
  `approvals/service.py`, so there is exactly one audit trail and no risk of
  a channel becoming a second, drifting approval system.
- **Secrets are encrypted per-tenant with a KEK the app reads, not embeds**
  — the KEK comes from the environment, not the codebase or the database, so
  a compromised database dump alone is not enough to decrypt anything.
- **A run is durable before it's queued** — intake commits the `AgentRun`
  row, then publishes to Redis; a crash between those two steps loses
  nothing, because there's nothing in the queue to lose yet.
- **Prompt caching is scoped to a department, never wider, and exact-match
  only** — the Redis key namespaces on `tenant_id` and `department_id` as
  literal segments before the hash, so a hash collision could never bridge
  two departments; a repeated question from a different agent in the same
  department is served from cache instead of a second LLM call, on by
  default and toggleable per department, with savings shown on the Cost
  page. Redis being slow or unreachable degrades to a normal model call, it
  never blocks or fails a run.
