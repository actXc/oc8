# Claude Agent SDK plugins as oc8 Capas

oc8 capas are a **superset** of the [Claude Agent SDK plugin standard](https://code.claude.com/docs/de/agent-sdk/plugins). A folder that follows Claude's layout can be dropped into `capas/` and discovered without conversion. Optional oc8 files (`plugin.toml`, `guardrails/`, `setup/`, runtimes, …) extend the same folder with governance and platform-specific features.

Implementation: `backend/src/oc8/capas/claude_adapter.py`, `backend/src/oc8/capas/claude_hooks/`.

## Three on-disk formats

Discovery assigns a `source_format` metadata field on the manifest:

| What is on disk | `source_format` | Default trust |
|-----------------|-----------------|---------------|
| `plugin.toml` only (native oc8) | `oc8` | As declared (often `first_party`) |
| Claude layout, no `plugin.toml` | `claude` | `community` |
| Both | `hybrid` | oc8 overlay wins |

The Capas API exposes `sourceFormat` and non-fatal `warnings` (e.g. unsupported Claude components) on discovered plugins.

## Folder layout

```text
my-capa/                          # capa id = folder name
├── .claude-plugin/plugin.json    # Claude manifest (optional; only `name` required)
├── skills/<slug>/SKILL.md        # Claude skills
├── agents/*.md                   # Claude subagents
├── hooks/hooks.json              # Claude lifecycle hooks
├── .mcp.json                     # Claude MCP servers
├── commands/*.md                 # Legacy Claude skills (optional)
│
├── plugin.toml                   # oc8 overlay (optional)
├── tool_pack.toml                # oc8 MCP (optional; merges with .mcp.json)
├── guardrails/                   # oc8-only — permission presets
├── setup/                        # oc8-only — connection setup form
├── runtime/ / sandbox/           # oc8-only — Docker runtimes
└── SOURCE.md                     # Attribution (recommended for third-party)
```

**Rule:** The folder name must match `plugin.toml` `[plugin].name` or `.claude-plugin/plugin.json` `"name"`.

## Hybrid merge precedence

When both Claude components and `plugin.toml` are present:

| Field | Winner |
|-------|--------|
| `trust`, `permissions`, `capabilities`, `sandbox`, `setup`, `guardrails` | oc8 `plugin.toml` |
| Skills, agents, MCP connections, Claude hooks | **Union** (duplicate skill names → discovery error) |
| `type` | oc8 if set explicitly, otherwise inferred from components |
| `agent_template` body | oc8 `[plugin.agent_template]` replaces Claude `agents/*.md` when present |

## Component mapping

| Claude source | oc8 manifest | Notes |
|---------------|--------------|-------|
| `plugin.json` `name`, `version`, `description`, `displayName` | `name`, `version`, `summary`, `label` | |
| `dependencies[]` | `plugin_depends` | Semver constraints ignored in v1 (warning only) |
| `skills/<slug>/SKILL.md` | `skill_pack.skills[]` | Also `skills/*.toml` in hybrid capas |
| `commands/*.md`, root `SKILL.md` | `skill_pack.skills[]` | Parsed via same rules as Claude Code |
| `agents/*.md` (one file) | `type=agent_template`, `agent_template` | Frontmatter → agent fields; body → `mission` |
| `agents/*.md` (two or more) | `type=department_template`, `department_template.agents[]` | |
| `.mcp.json` / inline `mcpServers` | `tool_pack.connections[]` | `${CLAUDE_PLUGIN_ROOT}` → absolute capa path |
| `hooks/hooks.json` | `claude_hooks` | Separate from oc8 `[plugin.handles]` Python hooks |

### Type inference (no explicit `type` in overlay)

1. Agents present → `agent_template` (one) or `department_template` (many)
2. Else MCP → `tool_pack`
3. Else skills → `skill`
4. Fallback → `agent_template`

## Skills: dual format in `skills/`

Native oc8 capas use `skills/<slug>.toml`. Claude capas use `skills/<slug>/SKILL.md`. A hybrid capa may use **both**; names must be unique across formats.

Skills from Claude sources may reference tools oc8 agents do not have (`bash`, `read`, `write`, …). Discovery does not reject them; warnings are surfaced so operators can judge before enable.

## MCP

Claude `.mcp.json` entries are normalised to oc8 `tool_pack.connections` (stdio transport, `config.command` / `args` / `env`). Connections are materialised **disconnected** on enable — same as native tool packs — until an operator completes setup or tests the connection.

For production integrations, prefer oc8 `tool_pack.toml` + `guardrails/` + `setup/` alongside or instead of `.mcp.json`.

## Hooks — two systems

oc8 has two hook mechanisms:

| System | Format | Execution |
|--------|--------|-----------|
| **oc8 hooks** | `[plugin.handles]` + Python `entry_points` | In-process (trusted) or sandbox worker (community) |
| **Claude hooks** | `hooks/hooks.json` | Runtime bridge at agent lifecycle events |

Claude hook **action types** and required consent permissions:

| Action type | Permission |
|-------------|------------|
| `command` | `hooks:claude:command` |
| `http` | `hooks:claude:http` |
| `mcp_tool` | `hooks:claude:mcp` |

Permissions are listed in the manifest (derived at discovery if omitted). Enable requires granting them explicitly.

### Claude events wired in oc8 v1

| Claude event | When oc8 fires it |
|--------------|-------------------|
| `SessionStart` | First step of the agent loop |
| `UserPromptSubmit` | Before each model call |
| `PreToolUse` | Before a tool call (can **block**; model sees an error) |
| `PostToolUse` | After successful tool execution |
| `PostToolUseFailure` | After tool error |
| `TaskCreated` | New task row opened |
| `Stop` | Run completes without further tool calls, or step limit reached |
| `SessionEnd` | Run teardown |

Command hooks run with `CLAUDE_PLUGIN_ROOT` set to the capa folder. A hook may return JSON with `"decision": "block"` to deny a tool call.

Events with no oc8 equivalent (worktree, monitors, LSP, themes, …) are ignored in v1; discovery may warn if declared in `plugin.json`.

## Compatibility matrix

| Claude component | Supported in oc8 v1 | oc8-only alternative |
|------------------|---------------------|----------------------|
| `skills/*/SKILL.md` | Yes | `skills/*.toml` |
| `agents/*.md` | Yes | `[plugin.agent_template]` |
| `.mcp.json` | Yes | `tool_pack.toml` + `guardrails/` |
| `hooks/hooks.json` | Yes (bridge) | `[plugin.handles]` + Python |
| `commands/` | Yes (as skills) | — |
| `output-styles`, `themes`, `monitors`, `lsp`, `bin/` | No (warning) | — |
| `guardrails/`, `setup/`, `runtime/` | — | Yes |

## Examples in the repo

**Claude-only fixture** (no `plugin.toml`):

```
backend/tests/plugins/fixtures/claude_only_plugin/
  .claude-plugin/plugin.json
  skills/review/SKILL.md
  agents/reviewer.md
  hooks/hooks.json
  .mcp.json
```

**Hybrid fixture** (Claude layout + oc8 overlay):

```
backend/tests/plugins/fixtures/claude_hybrid_plugin/
  .claude-plugin/plugin.json
  plugin.toml              # trust, permissions, agent_template override
  skills/extra_skill/SKILL.md
  agents/ignored.md        # superseded by plugin.toml agent_template
```

## Typical workflows

### Drop in an existing Claude plugin

1. Copy the plugin folder to `capas/<name>/`.
2. Ensure folder name matches `plugin.json` `name`.
3. Install + enable from Capas UI; grant hook permissions if present.
4. Optionally add `plugin.toml` later to set `trust = "first_party"`, guardrails, or setup.

### Extend a Claude plugin for oc8 production

```text
my-integration/
  .claude-plugin/plugin.json    # keep upstream metadata
  skills/ … agents/ …           # keep upstream skills/agents
  plugin.toml                   # oc8: trust, permissions, type
  tool_pack.toml                # replace or extend .mcp.json with guardrails seams
  guardrails/
  setup/
```

Keep Claude files for portability; use oc8 siblings for operator setup, approval thresholds, and permission presets.

## Related reading

- [capas/README.md](../../capas/README.md) — full manifest and folder reference
- [Claude Agent SDK plugins (official)](https://code.claude.com/docs/de/agent-sdk/plugins)
