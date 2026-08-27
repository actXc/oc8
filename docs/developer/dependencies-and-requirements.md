# Dependencies & Requirements

Two different mechanisms, both new, neither used by a shipped capa yet —
one names other *capas*, the other names *Python packages*.

## `plugin_depends` — capa-to-capa dependencies

```toml
[plugin]
plugin_depends = ["other_capa_name"]
```

Names other capas (by their `name`, matching their folder name) that
must be installed for this tenant before this one can be.

This is a **separate field** from the existing `depends` — `depends` is a
list of abstract *capability* strings the tenant's install must already
satisfy, checked at install time but never auto-resolved; `plugin_depends`
names concrete capas and **is** auto-resolved.

- **Auto-installs, never auto-enables.** Installing capa `P` recursively
  installs any not-yet-installed entry in `P.plugin_depends` first. The
  dependency ends up *installed but disabled* — exactly as if an operator
  had installed it manually and not clicked Enable yet. Enabling stays a
  separate, explicit decision for every capa, including auto-installed
  dependencies — auto-enabling would silently bypass the consent gate for
  something the operator never directly asked to activate.
- **A missing dependency is a clear install-time error**, never a silent
  skip — a `plugin_depends` entry naming a capa absent from disk fails
  loudly.
- **Cycles are caught at discovery time**, not install time: discovery
  builds the full `plugin_depends` graph across every discovered capa
  once, and a cycle marks every capa in it invalid with a clear error.
- **No version-constraint syntax.** Entries are bare capa names,
  presence-only — a capa either needs another capa installed or it does not. A capa
  needing a *specific version* of a dependency is a problem for a later
  design.

## Per-capa `requirements` — Python packages your code needs

Two different mechanisms, because the two kinds of capa code have
fundamentally different isolation properties. Getting this backwards
produces two different flavours of silent failure, so it's worth
understanding correctly before writing your first capa.

### MCP tool-bridge (subprocess) capas — real isolation

A bridge is already launched as a separate subprocess, so it can get
genuinely isolated dependencies without touching core's own Python
environment. Declared in `tool_pack.toml`:

```toml
[connections.config]
requirements = ["httpx>=0.27", "some-vendor-sdk>=2.0"]
```

Core's launch code wraps the configured `command`/`args` in `uv run --with
<requirements> -- <command> <args…>`, which resolves and caches an
ephemeral dependency overlay per invocation — nothing is installed into
the shared core environment. Two bridges can declare conflicting
requirement versions and neither affects the other, or the core process.

### In-process connector capas — diagnostic only, not installed

A connector's code runs in the *same* Python interpreter core itself
runs in — genuine per-capa isolation there would mean a separate
interpreter or venv per connector, well beyond what this mechanism does.
Declared in `plugin.toml`'s core section instead:

```toml
requirements = ["python-docx>=1.1", "openpyxl>=3.1"]
```

Before importing the capa's entry point, the loader checks: is each
declared requirement importable (and version-satisfying) in the *current*
backend environment? If anything is missing, the capa refuses to load
with an error naming exactly which package(s) are absent and that they
need to be added to `backend/pyproject.toml` — a core-maintainer action,
not something the capa or its author can do automatically.

**This is explicitly diagnostic, not installation.** Declaring
`requirements` here does not make anything get installed. It exists to
replace an opaque `ModuleNotFoundError` deep inside an import traceback
with a specific, named diagnosis at load time — closing exactly the gap
that once caused a real regression: an unrelated dependency-file edit
silently broke a shipped capa's document-extraction dependencies,
undetected until a full test run, because nothing had declared "this
capa needs these three packages" as a checkable fact.

## Next

- [Testing Capas](testing-plugins.md) for the fixture/import conventions
  this Python code runs under during tests.
- [Packaging & Distribution](packaging-and-distribution.md) for how
  `plugin_depends` interacts with install-from-disk.
