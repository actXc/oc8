# Setup Forms & OAuth

A capa whose connection needs configuration from an operator — an app
registration, a bot token, a service-account key — ships a `setup/`
folder. It's entirely optional: a capa with no configurable connection
has no `setup/` at all.

## Four fixed filenames

Unlike `guardrails/` (a folder of *N* same-shaped entries), `setup/` has
exactly four possible files, each playing a distinct, non-repeating role.
Discovery reads exactly these names — nothing else in the folder is
recognized.

### `fields.toml` — required whenever `setup/` exists

Carries the form itself: `title`, `description`, `submit_label`, the field
list, and **`validate_entry_point`** — the dotted `module:attr` path to
the capa's own credential-validation hook. `telegram_approvals/setup/fields.toml`:

```toml
title = "Telegram verbinden"
description = "Der Bot-Token von @BotFather. Verlässt den Secret Store nie unverschlüsselt."
submit_label = "Speichern & testen"
validate_entry_point = "channel.setup:validate"

[[fields]]
key = "bot_token"
label = "Bot-Token"
kind = "password"
required = true
secret_ref = "telegram/bot_token"
```

Note the dotted path: `channel.setup:validate` names the capa's *code
folder* (`channel/`, per [Capa Types](plugin-types.md)), never the
capa's own name — `telegram_approvals.setup:validate` would be wrong.
Every entry point anywhere in a manifest follows this rule.

### `validation.toml` — optional

The `any_of` rule, for a form where "at least one of these fields must be
filled in" — `google_workspace`'s setup needs either `shared_drive_ids` or
`delegated_mailboxes`:

```toml
any_of = [["shared_drive_ids"], ["delegated_mailboxes"]]
```

### `oauth_provision.toml` — optional

Present when submitting the form also provisions an OAuth connection —
names the provider, the connector type it configures, and (for
per-mailbox delegated access) which field carries the list of identities
and what environment-variable prefix their tokens get resolved under at
launch time.

### `mcp.toml` — optional

Present when submitting the form also wires up an MCP connection: the
connection key, launch command, and which form fields map onto which
`env`/`secret_env` entries and which department field.

`microsoft365` ships three of the four (no `validation.toml`);
`google_workspace` ships all four.

## Why `validate_entry_point` matters

Setup submission doesn't just save the form — it calls your capa's own
validation hook to actually test the credential (a real API call, not
"the string is non-empty") before the connection is created. This runs
through the same trusted entry-point mechanism as `entry_points`, so it's
covered by the same rules: only `first_party`/`verified` capas get
theirs called, and a dotted path pointing at a nonexistent module fails
loudly at discovery time, not silently at first submission.

## Credentials, again

As with a connector's config (see
[Manifest Reference](manifest-reference.md#credentials)), a setup field
never carries a raw secret through to storage: a `kind = "password"` field
with a `secret_ref` is written to the tenant's encrypted secret store, and
the source config keeps only the reference. OAuth-backed setup goes
through `oauth_provision.toml` instead — the operator never types a token
by hand.

## Next

- [Guardrails & Permissions](guardrails-and-permissions.md) for the other
  optional folder a connection-bearing capa usually ships.
- [Testing Capas](testing-plugins.md) for how to prove
  `validate_entry_point` survived a manifest split intact — a typo'd
  dotted path is invisible to every check except actually resolving it.
