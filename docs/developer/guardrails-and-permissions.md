# Guardrails & Permissions

A guardrail is the permission ceiling a connection's tools run under —
what's readable, what's writable, what needs a euro threshold before it
needs a human, and which tools are reachable at all. Every guardrail file
lives in a capa's `guardrails/` folder, one file per entry.

## Two shapes, one folder

Guardrail files come in two genuinely different pydantic models, and every
file declares which one it is with a required `kind` discriminator:

### `kind = "preset"` — a fixed ceiling on one connection

A `GuardrailPreset` is one of a capa's ready-made permission levels,
offered as a pick-list an operator can choose instead of hand-deriving one
from a raw tool list. `microsoft365/guardrails/read_only.toml`:

```toml
kind = "preset"
connection = "primary"
key = "read_only"
label = "Nur lesen"
label_en = "Read only"
summary = "Darf lesen und suchen, aber nichts verändern."
summary_en = "Can read and search, but change nothing."
recommended = false
read = true
write = false
send = false
approval_actions = []
approval_eur = ""
```

`connection` names which `tool_pack.toml` connection the preset belongs
to. **When omitted, the preset binds to the capa's sole connection** —
whatever that connection's real `key` is, never the literal string
`"primary"`. Only set `connection` explicitly once a capa declares two
or more connections.

### `kind = "library"` — a documented use-case scenario

A `Guardrail` is a named, documented scenario (`use_case: str`, required)
attached to the capa as a whole rather than to one connection, with an
`adjustable` list of parameters an operator is expected to tune.
`odoo_mcp/guardrails/cross_never_deletes.toml` (one of its 28 entries):

```toml
kind = "library"
key = "cross_never_deletes"
label = "Nie löschen, über alle Bereiche hinweg"
label_en = "Never delete, across every area"
summary = "Legt Datensätze an, bearbeitet und versendet sie, aber delete_record steht nie zur Verfügung ..."
summary_en = "Creates, updates and sends records, but delete_record is never available ..."
use_case = "cross_cutting"
read = true
write = false
send = true
approval_eur = ""
approval_actions = []
only = ["search_records", "get_record", "list_models"]
```

## The one rule that isn't optional

**The filename (minus `.toml`) must equal the guardrail's own `key`
field**, regardless of `kind`. A mismatch is a hard discovery error, not a
silent rename — this is the same "folder name must equal manifest `name`"
consistency check applied one level deeper, and it exists for the same
reason: a renamed file silently pointing at the wrong config is worse than
a capa that refuses to load.

## `only` is not decoration

`only` is the list of tool names a preset or library entry actually puts
within reach — empty means every tool the connection has. It's what makes
a preset like `autonomous_with_limit` (write+send above a euro threshold)
actually safe: the threshold only protects the tools that carry a monetary
value. A deletion carries no amount and could never meet the threshold —
*unless* the preset also reaches a delete tool, in which case the
threshold means nothing for that action. Every guardrail-consuming code
path (the manifest model, the DTO, the frontend picker) has to carry
`only` through unmodified; dropping it anywhere turns "autonomous with a
limit" back into "everything reachable above €1000," under a name that
promises otherwise.

## Order is cosmetic, not semantic

Because each guardrail is its own file, discovery reads a capa's
`guardrails/` folder as a sorted directory glob — the order presets and
library entries appear in is alphabetical by key, not the order you wrote
the files in. Nothing in core reads guardrail order as meaningful (the one
UI component that lists presets sorts `recommended` first regardless), so
this is a cosmetic detail, not something to work around by choosing
filenames carefully.

## Next

- [Setup Forms & OAuth](setup-forms-and-oauth.md) — where a connection's
  fields, and any per-field validation, live.
- [Manifest Reference](manifest-reference.md) for the surrounding
  `tool_pack.toml` shape a preset's `connection` field points at.
