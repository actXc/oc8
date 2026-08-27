# Packaging & Distribution

## Trust levels

```toml
[plugin]
trust = "first_party"   # first_party | verified | community
```

`trust` decides whether a code-carrying capa is imported at all — see
[Capa Types](plugin-types.md#rules-that-apply-to-every-code-carrying-capa).
`first_party` and `verified` are imported; `community` is never imported
today, because running untrusted code needs sandbox isolation that
doesn't exist yet. A manifest-only capa (a department template, a
skill, a flow) has no code to import, so `trust` still matters for
consent, but not for import safety.

## Installing

1. Put the folder in the capas path (below).
2. Open **Capas** in the UI — the capa appears under *available*.
3. Press **Install**. That installs it **for your tenant only**.

Or via the API:

```
GET  /api/v1/capas/available
POST /api/v1/capas/install-from-disk   {"pluginId": "vertrieb_bundle"}
```

Installing requires the `org_admin` role. The request names a capa
**id**, never a path — the capas path is operator configuration, so
nobody can install something the operator hasn't placed on disk.

Discovery is **installation-wide** (every tenant sees the same available
folders); installation is **per tenant** (one tenant installing doesn't
install it for anyone else). If a capa declares `plugin_depends`,
installing it also installs — but does not enable — any not-yet-installed
dependency first; see [Dependencies & Requirements](dependencies-and-requirements.md).

A capa that ships code needs one more step: **enable** it, which grants
its declared permissions. Data-only bundles work as soon as they're
installed.

## Configuring the capas path

`OC8_CAPAS_PATH`, default `capas`, comma-separated for several roots.
When the same capa id appears in more than one root, the **first** root
wins and the later one is shadowed.

The default is relative to the process working directory. In Docker
that's `/app`, and `docker-compose.yml` mounts the capas directory
read-only to `/app/capas`. Running the backend locally from `backend/`,
set `OC8_CAPAS_PATH=../capas` (or an absolute path) — otherwise it
looks for `backend/capas` and finds nothing.

## Changing a capa

Discovery re-scans on every request, so editing a `plugin.toml` (or any of
its sibling `tool_pack.toml`/`guardrails/`/`setup/`/`i18n/` files) shows up
immediately in the *available* list. An already-installed capa is a
database snapshot of the manifest at install time — bump `version` and
install again to pick up changes for tenants that already installed it.

## Next

- [Capa Types](plugin-types.md) for what each `type` does once it's
  installed and enabled.
- [Contributing → Architecture](../contributing/architecture.md) if you
  want to see the loader/discovery mechanics that make all of this work.
