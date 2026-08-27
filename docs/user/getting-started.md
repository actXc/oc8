# Getting started

> **Install oc8 yourself?** Start with the step-by-step
> [Quickstart — install and run](quickstart.md).

This guide covers alternative paths and troubleshooting after you have a running
instance.

## Choose your path

| Path | Best for | Time |
|------|----------|------|
| **Quickstart script** | Operators evaluating oc8 on a laptop or VM | ~10 min |
| **Full Compose stack** | Same, with explicit control over `.env` | ~15 min |
| **Manual backend dev** | Core contributors debugging the runtime | ~30 min |

---

## Path A — Quickstart (recommended)

From the repository root:

```bash
./scripts/quickstart.sh          # macOS / Linux
# Windows PowerShell:
.\scripts\quickstart.ps1
```

The script checks Docker, creates missing secrets in `.env`, builds and starts
the stack, waits for `/health`, and prints the local URL.

1. Open the URL in your browser.
2. Walk through the **welcome wizard** — organisation, department, agent,
   model, tool, and guardrails (all in the UI).
3. Run a first task from **Office** or the agent page; approvals land in
   **[My work](ui/my-work.md)**.

You only need **Settings → Models** or `.env` keys if you skipped the wizard or
want a provider configured outside the UI flow.

→ [Welcome wizard](ui/welcome.md) · [Quickstart](quickstart.md) (full steps)

For a demo stack with seeded tenants and dev-login (local only, never expose
to a network):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

---

## Path B — Docker Compose (explicit)

```bash
cp .env.example .env
# Edit .env: set OC8_JWT_SECRET, OC8_SECRET_KEK, POSTGRES_PASSWORD (see DEPLOY.md)

docker compose up -d --build
```

Follow [Deploy](../DEPLOY.md) for ports and production hardening. Then use the
welcome wizard in the browser (same as Path A).

---

## Path C — Manual developer setup

For running the API and worker outside Compose (fast iteration on backend code):

1. Read [First run (API-level)](../FIRST_RUN.md) — Postgres, migrate, seed,
   uvicorn, worker, curl-based agent + MCP filesystem demo.
2. Read [Contributing: architecture](../contributing/architecture.md) if you
   will change core code.

---

## After your first run

| Next step | Document |
|-----------|----------|
| Understand vocabulary (department, capa, frame, …) | [Key concepts](key-concepts.mdx) |
| Day-to-day operator workflow | [Daily workflow](daily-workflow.md) |
| Approvals and autonomy | [Governance and approvals](governance-and-approvals.md) |
| Install on a server | [Install and maintain](install-and-maintain/index.md) |
| Build an integration | [Developer: first capa](../developer/tutorial-first-plugin.md) |

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| Run stays `queued` | Worker not running — `docker compose ps worker` or start `oc8 worker` manually |
| Model errors / timeout | Provider key missing, or Ollama not running / wrong model tag |
| No tools available | MCP connection not configured, or capa not installed/enabled |
| 401 on API | Token expired — re-login; in dev use `/auth/dev-login` only on localhost |

More detail: [Scope and limitations](../SCOPE_AND_LIMITATIONS.md).
