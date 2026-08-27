# Manifest Reference

## `plugin.toml`

The `[plugin]` table maps 1:1 onto oc8's manifest schema:

```toml
[plugin]
name = "vertrieb_bundle"      # must equal the folder name
version = "1.0.0"             # semver; installing the same version twice is rejected
type = "department_template"  # see Capa Types
trust = "first_party"         # first_party | verified | community
summary = "One line, shown in the Capas UI"
core_compat = ">=0.1"         # optional PEP 440 specifier against the core version
permissions = []               # a LIST of permission strings
depends = []                   # capabilities that must already be present, or install fails
plugin_depends = []            # OTHER CAPAS this one needs installed first
requirements = []              # PEP 508 strings this capa's IN-PROCESS code needs
```

Two things to know before you write your first one:

- **Unknown keys are a hard error.** The schema is strict — a typo fails
  loudly instead of being silently ignored. If a field isn't listed here
  or in `backend/src/oc8/capas/manifest.py`, it doesn't exist.
- **`permissions` is a list**, not a table.

`plugin.toml` itself carries only the core identity fields plus whichever
of `entry_points`, `handles`, `sandbox`, `department_template`,
`skill_template`, `skill_pack`, `flow_template`, `config`, `tools`, `mcp`,
`skills`, `triggers`, `policy`, `io`, `phases`, `hooks` your capa's
`type` actually uses. Everything else — the MCP connection, the
guardrails, the setup form — lives in its own file or folder, described
below.

## The manifest split

Discovery assembles one pydantic `Manifest` object from whichever of these
files/folders are present — nothing downstream of discovery cares that the
manifest used to be one file. You only ship the pieces your capa
actually needs.

```
capas/<name>/
    plugin.toml              # required — the slim core manifest
    tool_pack.toml            # optional — MCP connection wiring
    guardrails/                 # optional — one file per preset/library entry
    setup/                        # optional — present only if the capa has a setup form
    i18n/                           # optional — translation catalogs
    connector/ | runtime/ | provider/ | channel/    # optional — see Capa Types
    mcp_bridge/                                       # optional — MCP tool bridge
    tests/                                              # the capa's own pure-unit tests
```

### `tool_pack.toml`

If a capa exposes MCP tools, its connection wiring lives in a sibling
`tool_pack.toml`. **This file's own top level *is* the `tool_pack` table**
— there's no `[plugin.tool_pack]` wrapper to nest under. `microsoft365`'s
real file:

```toml
[[connections]]
key = "primary"
name = "microsoft365"
transport = "stdio"
server_url = ""

[connections.scopes]
read = ["mail_search", "mail_get", "..."]
send = ["mail_send", "mail_reply", "..."]

[connections.config]
command = "python"
args = ["-m", "mcp_bridge"]
outward_tools = ["mail_send", "mail_reply"]

[connections.config.env]
PYTHONPATH = "/app/capas/microsoft365"

[connections.config.secret_env]
GRAPH_ACCESS_TOKEN = "oauth:graph_access_token"
```

`outward_tools` and `scopes` are how a connection tells core which tools
reach outside the tenant and which ones are read vs. write vs. send — see
[Architecture Overview](architecture-overview.md#how-a-capas-tools-get-called-during-a-real-run)
for how core reads these at call time.

## Why the bridge folder is `mcp_bridge/`, never `mcp/`

The obvious first instinct is to name an MCP tool bridge's folder `mcp/` —
but `mcp` is also the name of the installed MCP SDK (`mcp>=1.2,<3`, a hard
backend dependency), and every bridge does `import mcp.types` /
`from mcp.server import Server`. A bridge is launched as a subprocess with
its own capa root on `PYTHONPATH`
(`microsoft365/tool_pack.toml`'s `connections.config.env.PYTHONPATH =
"/app/capas/microsoft365"` is the real, shipped value), and once that
root is on the path, a local package literally named `mcp/` **shadows the
SDK** — the bridge's own `import mcp.types` resolves to itself instead of
the installed package. The failure surfaces as the subprocess dying at
startup with `ModuleNotFoundError: No module named 'mcp.types'`, well
after anyone is looking at folder names. `mcp_bridge/` reads just as
clearly and can never collide with an installed dependency.

## The old flat layout is rejected, not ignored

This was a hard cutover. An inline `[plugin.tool_pack]` table, an inline
`[plugin.setup]` table, or a flat `guardrails.toml` file at a capa's
root is a **hard discovery error** naming exactly where that content
moved to — never a silent skip. A silently-ignored leftover would quietly
strip a capa of its guardrails or its whole setup form; that's worse
than refusing to install it. If you see an error like `"[plugin.tool_pack]
has moved out of plugin.toml into a sibling tool_pack.toml"`, that's the
mechanism working as designed — split the file rather than working around
the error.

## Credentials

Never put a credential in the source config — it's stored as plain JSON on
the `data_source` row. Take one of two routes instead:

- **OAuth** — set `requires_oauth = "google"` (or another registered
  provider) on your `Connector`, and call `await auth.token()`. You get a
  fresh access token; refresh, expiry, and storage are none of your
  business. `gdrive_source` does this.
- **Anything else** — put the *name* of a stored secret in your config
  (e.g. `accessKeyRef`) and call `await auth.secret(name)`. The value
  lives in the tenant's encrypted secret store. `s3_source` does this.

Either way, a leaked `data_source` row leaks a reference, not a key.

## Next

- [Guardrails & Permissions](guardrails-and-permissions.md) for `guardrails/*.toml`.
- [Setup Forms & OAuth](setup-forms-and-oauth.md) for `setup/`.
- [Dependencies & Requirements](dependencies-and-requirements.md) for
  `plugin_depends` and `requirements`.
