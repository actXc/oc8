# Architecture Overview

## The microkernel principle

oc8 is built as a **microkernel**: a small, generic core plus everything
else as capas. This is the single most important structural rule in the
codebase, and it exists to answer one question consistently: *where does
this code go?*

- **Core** provides mechanisms and stable extension points — the agent run
  loop, tool dispatch, the authorization decision point, the approval
  workflow, the capa loader and contribution registry, connections,
  secrets, multi-tenancy. Core **names no specific product and assumes no
  specific use case.**
- **Capas** carry everything specific — every integration (Microsoft 365,
  Google Workspace, Odoo, an S3 bucket), every business scenario (a sales
  department, a helpdesk agent), every guardrail preset tuned to a real
  workflow.

The test for any change: *is this a generic mechanism, or is it specific to
a piece of software or a use case?* Mechanism → core, as a neutral seam.
Specific → a capa.

Two real examples of that seam, straight from the code:

**`agent/tool_semantics.py`** decides where a tool call's monetary value
lives (for the approval threshold) and how to describe the record it
touched (for the live log) — without knowing anything about the systems
whose tools it's looking at:

> "The core must stay software-agnostic: it names no product, model, or
> field. But two things about a tool call ARE software-specific... A
> connection whose tools need that supplies a declarative spec in its
> config; the plugin that created the connection owns those specs, so all
> specifics live in the plugin (data), never here."

**`agent/outward.py`** enforces "at most one outward message per record per
task" — a real safety rule — without knowing what an outward message even
is for any given system:

> "The core does not know which tools reach a person — that is
> software-specific... A connection declares it:
> `[plugin.tool_pack.connections.config] outward_tools = ["post_message"]`.
> An empty declaration leaves the guard inert."

Same pattern in `channels/`, the package approval channels (Telegram,
WhatsApp) plug into: "Core defines the seam and owns the record. Which
messengers exist is a plugin question — nothing in this package names
one."

If you're writing a capa, this principle mostly shows up as a
constraint in the other direction: **core will never grow a special case
for your integration.** Whatever's specific to your system belongs in your
capa's manifest (as declarative data) or your capa's code — never as a
patch to a core module.

## Where a capa's code runs

A capa contributes to one of a handful of **registries** core already
knows how to call — it never gets ad hoc hooks into arbitrary core
internals. The three shapes most capas use:

- A **connector** contributes a `Connector` (knowledge-source ingestion) to
  the connector registry.
- An **MCP tool bridge** is launched as its own subprocess, speaking the
  Model Context Protocol; core talks to it exactly like it would talk to
  any external MCP server.
- A **runtime adapter** / **model adapter** / **approval channel**
  contribute an implementation of a core-defined protocol (agent runtime,
  LLM provider, approval delivery channel) to the matching registry.

See [Capa Types](plugin-types.md) for the full list and what each one
needs.

## How a capa's tools get called during a real run

This is the part most capa authors never have to think about, but it
explains *why* the manifest looks the way it does. The agent execution
loop (`agent/engine.py`) is, in one sentence: **trigger → assemble context
→ ask the model → for each tool call the model wants, authorize it, then
invoke it → feed the result back → repeat until the model stops.**

For a tool call against your capa's MCP connection, that expands to:

1. **Authorize.** The Policy Decision Point (`authz/`) checks the tool
   call against the agent's effective guardrail — the same `read`/`write`/
   `send`/`only`/`approval_eur` fields your `guardrails/*.toml` files
   declare. A call that exceeds the ceiling is either refused or raises a
   human-in-the-loop approval, depending on the guardrail.
2. **Invoke.** Core calls your MCP bridge (a subprocess, launched with the
   `command`/`args`/`env`/`secret_env` your `tool_pack.toml` declares) or
   your in-process connector, and gets a result back.
3. **Classify the result.** `tool_semantics.py` and `outward.py` (above)
   read your connection's `value_spec`/`focus_spec`/`outward_tools` — pure
   data your capa declared — to decide what to show in the live log and
   whether this call counts as "reaching a person."
4. **Audit and meter.** Every tool call is appended to the immutable audit
   trail and metered against the tenant's token budget, regardless of
   which capa it belongs to.

None of this is something your capa's code implements — it's why the
manifest asks for `outward_tools`, `value_spec`, and guardrail fields as
*data* instead of asking your code to implement authorization or logging
itself. Write your connector or bridge to do the actual work; describe the
rest declaratively.

## Multi-tenancy, briefly

Every tenant-scoped database table is protected by Postgres row-level
security, driven by a single transaction-local setting
(`app.tenant_id`) that a bound session sets once per transaction. Tables
default to **fail-closed**: no `tenant_id` set means no rows, not "return
everything." As a capa author you don't interact with this directly —
your connector code runs inside whatever tenant-scoped session core hands
it — but it's worth knowing that tenant isolation is enforced at the
database layer, not by an `if tenant_id ==` check scattered through
application code.

## Next

- [Capa Types](plugin-types.md) for the full type/folder table.
- [Tutorial: Your First Capa](tutorial-first-plugin.md) to build one.
- [Contributing → Architecture](../contributing/architecture.md) if you
  want the same picture from the core-maintainer side, including the
  subsystem map and the loader/discovery mechanics.
