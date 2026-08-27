# First Run — a local agent that processes a task end to end

This runbook takes a fresh checkout to a **working first agent run**: the local
stack up, an agent created, an MCP connection, and a real task executed that
produces a real side effect (a file on disk). Verified end to end on 2026-07-20
against a local Ollama model — no API key, no cloud.

What the run proves: `POST /agents` → `POST /agents/{id}/run` → the durable
worker → `run_agent` → the agent connects to an MCP server over stdio, the LLM
decides to call a tool, and the tool writes a file.

## 0. Prerequisites

- **Docker** (for Postgres + Redis via compose).
- **uv** (Python env/runner) — the backend uses it.
- An **LLM the agent can reach**. Two options:
  - **Local, free (recommended for a first run): Ollama.** `brew install ollama`
    (or https://ollama.com). No API key, nothing leaves the machine.
  - **Cloud:** set `OC8_ANTHROPIC_API_KEY` (or `OC8_OPENAI_API_KEY`) and point
    the agent at a Claude/GPT model config instead of the Ollama one.

## 1. Bring up the data stores + migrate + seed

```bash
cd backend
docker compose up -d                      # Postgres (:5433) + Redis (:6381)
# migrate the dev DB to head (idempotent):
OC8_MIGRATION_URL="postgresql+psycopg://oc8_migrate:oc8@localhost:5433/oc8" uv run alembic upgrade head
uv run oc8 seed --reset                   # ACME + GLOBEX tenants, agents, a demo MCP connection
```

The seed creates a tenant (`ACME Industries`), departments, ~16 agents, model
configs, and — importantly — a **demo MCP connection** ("Filesystem (demo)")
that launches `mcp_servers/demo_fs.py` over stdio with `read_file`/`write_file`/
`list_files` tools, sandboxed to `/tmp/oc8-workspace`.

## 2. Start the LLM (Ollama path)

```bash
ollama serve &                            # starts the local model server on :11434
ollama pull llama3.1:8b                    # ~4.7GB; or a smaller one, e.g. llama3.2:3b (~2GB)
```

**Known seed gap (see §7):** the seeded Ollama model config stores a *display
name* as its model, which Ollama won't recognize. Point it at the tag you
pulled:

```bash
docker exec oc8-postgres psql -U oc8_migrate -d oc8 -c \
  "UPDATE model_config SET provider='ollama', model='llama3.1:8b' WHERE model LIKE '%local%';"
```

(Use whatever tag you pulled. `ollama list` shows installed tags.)

## 3. Start the control plane + the durable worker

Two processes (the worker is not in compose yet — start it yourself):

```bash
cd backend
uv run uvicorn oc8.main:create_app --factory --port 8099 --log-level info    # terminal A
uv run oc8 worker                                                            # terminal B (drains the run queue)
```

Health check: `curl -s localhost:8099/health` → `{"status":"ok",...}`.

## 4. Get a token + the ids you need

The API is dev-authed (a `POST /auth/dev-login` mints a token for the seeded
tenant — a low-friction path for local development, alongside real password
auth via `POST /auth/setup` / `POST /auth/login`).

```bash
T=$(curl -s -X POST localhost:8099/api/v1/auth/dev-login -d '{}' -H 'content-type: application/json' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# a department to put the agent in:
curl -s localhost:8099/api/v1/departments -H "authorization: Bearer $T" | python3 -c 'import sys,json;[print(d["id"],d["name"]) for d in json.load(sys.stdin)]'
# the Ollama model config id + the demo MCP connection id:
docker exec oc8-postgres psql -U oc8_migrate -d oc8 -tA -c "SELECT id,model FROM model_config WHERE provider='ollama';"
docker exec oc8-postgres psql -U oc8_migrate -d oc8 -tA -c "SELECT id,name FROM mcp_connection;"
```

## 5. Create the agent (POST /agents)

Request bodies are camelCase. Point the agent at the Ollama model config.

```bash
curl -s -X POST localhost:8099/api/v1/agents -H "authorization: Bearer $T" -H 'content-type: application/json' -d '{
  "name": "First E2E Runner",
  "departmentId": "<a department id from step 4>",
  "roleTitle": "test",
  "mission": "Prove the runtime end to end by writing a file via the MCP filesystem tool.",
  "modelConfigId": "<the ollama model config id>"
}'
# note the returned "id"; then start it:
curl -s -X POST localhost:8099/api/v1/agents/<AGENT_ID>/lifecycle -H "authorization: Bearer $T" -d '{"action":"start"}'
```

Note: MCP tools are authorized by the **MCP connection's scopes**, not the
agent's department frame — so a Sales-department agent can still use the demo
filesystem tools. (The department frame gates the built-in tool categories like
email/hubspot.)

## 6. Run a task with the MCP connection

```bash
curl -s -X POST localhost:8099/api/v1/agents/<AGENT_ID>/run -H "authorization: Bearer $T" -H 'content-type: application/json' -d '{
  "task": "Use the write_file tool to create hello.txt with content: Hello from oc8 first end-to-end run.",
  "mcpConnectionId": "<the demo MCP connection id>"
}'
# poll the run (the first Ollama inference is slow — the model loads into RAM):
curl -s localhost:8099/api/v1/runs/<RUN_ID> -H "authorization: Bearer $T"
```

When `state` is `done`, verify the real side effect:

```bash
cat /tmp/oc8-workspace/hello.txt        # -> "Hello from oc8 first end-to-end run"
```

The worker log shows the MCP round-trip: `ListToolsRequest` → `CallToolRequest`.
That's the whole chain working: agent created → MCP connected → LLM chose the
tool → the tool ran and wrote a file.

## 7. What was actually missing (and gotchas)

- **Ollama must be running** (`ollama serve`) with a model pulled. Installed but
  not running is the most common "nothing happens".
- **Seed gap — the Ollama model config stores a display name, not a valid tag.**
  Fixed by hand in §2; a proper seed fix should store a real tag (e.g. read
  `settings.default_model`). Tracked separately.
- **The worker is a separate manual process** (`oc8 worker`) — not in
  docker-compose. If a run stays `queued`, the worker isn't running.
- **First inference is slow** (the model loads into RAM). Subsequent runs are
  fast. A small model (`llama3.2:3b`) is quicker to load than `mistral:latest`.
- **Cloud instead of local:** set `OC8_ANTHROPIC_API_KEY` and point the agent's
  `modelConfigId` at a Claude model config (`SELECT id FROM model_config WHERE
  provider='anthropic';`). No Ollama needed then.
