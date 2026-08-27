# Tutorial: Your First Capa

This builds a small, real, working **connector** capa — a knowledge
source that reads plain-text files from a folder on disk. It needs no
external API, no OAuth, no credentials, so you can run every step exactly
as written. The real thing this mirrors is
[`capas/gdrive_source/`](../../capas/gdrive_source/) — read that one
next once this tutorial clicks, since it's the same shape solving a real
integration.

If you haven't yet, read [Architecture Overview](architecture-overview.md)
first — this tutorial assumes you know what "core" vs. "capa" means and
why the code folder is named `connector/`, not after the capa.

## 1. The folder

```bash
mkdir -p capas/local_notes/connector capas/local_notes/tests
```

Every capa folder's name **is** its capa id, and must equal `name` in
its manifest — a mismatch is a hard error, not installed.

## 2. `plugin.toml`

```toml
[plugin]
name = "local_notes"
version = "1.0.0"
type = "connector"
trust = "first_party"
summary = "Reads plain-text notes from a local folder as a knowledge source."
permissions = ["knowledge:write"]

[plugin.entry_points]
connectors = "connector.connector:register"
```

`type = "connector"` and the `connectors` entry point are the two things
that make this a connector capa. `permissions` is a list — `local_notes`
needs to write knowledge, so it declares `knowledge:write`; an operator
sees this at enable time and it's the whole consent story.

## 3. The connector itself

Every connector implements one protocol
(`oc8.knowledge.connectors.base.Connector`): four attributes, three async
methods.

`capas/local_notes/connector/connector.py`:

```python
"""Reads *.txt files from a local folder as knowledge documents."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from oc8.knowledge.connectors.base import (
    AuthContext,
    RawDocument,
    SourceItemMeta,
    ValidationResult,
)


class LocalNotesConnector:
    type_id = "local_notes"
    label = "Local notes folder"
    description = "Plain-text files from a folder on the server's disk."
    config_schema: dict[str, Any] = {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string"}},
    }
    requires_oauth = None  # no OAuth provider needed

    async def validate(
        self, config: dict[str, Any], auth: AuthContext | None = None
    ) -> ValidationResult:
        path = Path(config.get("path", ""))
        if not path.is_dir():
            return ValidationResult(ok=False, error=f"{path} is not a directory")
        return ValidationResult(ok=True)

    async def discover(
        self, config: dict[str, Any], auth: AuthContext | None = None
    ) -> list[SourceItemMeta]:
        path = Path(config["path"])
        return [
            SourceItemMeta(uri=str(f), title=f.name)
            for f in sorted(path.glob("*.txt"))
        ]

    async def fetch(
        self,
        config: dict[str, Any],
        cursor: dict[str, Any] | None,
        auth: AuthContext | None = None,
    ) -> AsyncIterator[RawDocument]:
        path = Path(config["path"])
        for f in sorted(path.glob("*.txt")):
            content = f.read_text(encoding="utf-8")
            yield RawDocument(
                source_uri=str(f),
                title=f.name,
                content=content,
                content_type="text/plain",
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
            )


def register(contrib: Any) -> None:
    contrib.add_connector(LocalNotesConnector())
```

Notes on the shape, since these are the parts a first-time author gets
wrong:

- `validate` checks the config is usable *before* anything is saved — it
  runs when an operator tests the connection, and never touches network or
  disk beyond what's needed to answer "does this work."
- `discover` is a **preview** — return a sample, it's allowed to be
  incomplete (`website.py`'s real connector returns exactly one item, the
  start URL). It is not a promise about what `fetch` will return.
- `fetch` is an **async generator** (`yield`, not `return`) — this is what
  lets a connector stream thousands of documents without holding them all
  in memory at once.
- `content_hash` is what oc8's ingestion pipeline uses to detect an
  unchanged document on the next sync and skip re-embedding it — always
  compute it from the actual content, never from metadata like a
  modification time (a file touched but not edited would re-ingest for no
  reason).
- `register(contrib)` is called once per process. `contrib.add_connector`
  is the one call this capa needs; a capa contributing more than one
  thing (say, a connector *and* a runtime) would call more than one
  `add_*` method here.

`register`'s type is `Any` deliberately — the real
`PluginContributions` type lives in core
(`oc8.capas.contributions`), and a capa only needs to know its shape
(`add_connector`, `add_runtime`, `add_model_provider`, `add_hook`), not
import internals beyond that one class.

## 4. Prove it registers

`capas/local_notes/tests/test_connector.py`:

```python
from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _evict() -> None:
    for name in [n for n in sys.modules if n == "connector" or n.startswith("connector.")]:
        del sys.modules[name]


@pytest.fixture(autouse=True)
def _plugin_path() -> Iterator[None]:
    sys.path.insert(0, str(PLUGIN_ROOT))
    _evict()
    yield
    sys.path.remove(str(PLUGIN_ROOT))
    _evict()


def test_register_contributes_the_local_notes_connector() -> None:
    from connector.connector import register

    class _Contrib:
        def __init__(self) -> None:
            self.connectors: dict[str, object] = {}

        def add_connector(self, connector: object) -> None:
            self.connectors[connector.type_id] = connector  # type: ignore[attr-defined]

    contrib = _Contrib()
    register(contrib)
    assert "local_notes" in contrib.connectors
```

Why the fixture, when this is the only capa using the name `connector/`
in your local checkout: **every real capa folder shares that same
generic name**, and `sys.modules` is keyed by name, not by path — a second
connector capa's test, collected in the same session, would otherwise
silently get *this* capa's code. Copy this fixture verbatim into any new
capa; see [Testing Capas](testing-plugins.md) for the full explanation
and the function-local-import variant.

Run it:

```bash
cd backend && uv run pytest -c pyproject.toml ../capas/local_notes/tests -v
```

(The `-c pyproject.toml` is required whenever every argument to `pytest`
lives outside `backend/` — otherwise pytest can't find its own config and
silently loses the settings that make the folder collectable at all. See
[Testing Capas](testing-plugins.md).)

## 5. See it discovered

```bash
cd backend && uv run python -c "
from oc8.capas.discovery import find_plugin
p = find_plugin('local_notes', paths=['../capas'])
print(p.valid, p.error)
"
```

Should print `True None`. `paths=['../capas']` is needed here because
`find_plugin` without it falls back to `OC8_CAPAS_PATH` (default:
`capas`, relative to the process's working directory) — from `backend/`
that resolves to `backend/capas`, not the repo-root `capas/` you just
created a folder in. The running server has `OC8_CAPAS_PATH` configured
correctly (see [Packaging & Distribution](packaging-and-distribution.md#configuring-the-capas-path));
a one-off script like this one doesn't inherit that unless you export it
yourself, so pass `paths` explicitly instead.

If it prints an error instead of `None`, it's telling you exactly what's
wrong — read it; `discovery.py` is deliberately loud, never a silent
skip.

## 6. What's next

- Give it a **guardrail** or a **setup form**? Neither applies to a bare
  connector — those are for capas with a `tool_pack.toml` (MCP tools).
  See [Guardrails & Permissions](guardrails-and-permissions.md) and
  [Setup Forms & OAuth](setup-forms-and-oauth.md) when you get there.
- Need credentials? See the **Credentials** section of
  [Manifest Reference](manifest-reference.md) — OAuth via `AuthContext.token()`,
  or a stored secret via `AuthContext.secret(ref)`. Never put a credential
  in the config dict itself.
- Read [`capas/gdrive_source/connector/connector.py`](../../capas/gdrive_source/connector/connector.py)
  for the real thing: OAuth, an `attest_listing` implementation (the
  optional protocol half that lets core know a listing is complete), and
  real error handling against a real API.
