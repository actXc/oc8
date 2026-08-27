# Testing Capas

## Where a capa's tests live

Pure-unit tests — no database, no FastAPI app, importing capa code
directly — live inside the capa, at `capas/<name>/tests/`, and are
collected as part of the normal `cd backend && uv run pytest` run. Tests
that need oc8's app/DB fixtures (`app_session`, `AppSessionFactory`, and
the rest of `backend/tests/conftest.py`'s chain — those fixtures only
apply to tests collected under `backend/tests/`) stay where they always
lived, at `backend/tests/plugins/<name>/`.

If your capa never touches the database or the running app directly,
its tests belong in `capas/<name>/tests/`. If a test needs a real
tenant, a real installed capa row, or the FastAPI app, it belongs under
`backend/tests/plugins/<name>/` instead.

## The settings that make `capas/<name>/tests/` collectable at all

`backend/pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests", "../capas"]
addopts = "--import-mode=importlib"
consider_namespace_packages = true
```

Under pytest's *default* `prepend` import mode, a capa-local `tests/`
folder can't be collected at all, two independent ways: with a
`tests/__init__.py` per capa, the top-level package name `tests` is
already owned by `backend/tests/__init__.py` and collection dies with
`ModuleNotFoundError: No module named 'tests.test_connector'`; without
one, two capas' identically-named test files collide with `import file
mismatch` (several capas share test basenames like `test_connector.py`
or `test_manifest.py`). `--import-mode=importlib` imports each test file
under a path-derived name instead of a `sys.path`-derived one, which fixes
both; `consider_namespace_packages` keeps `backend/tests`' own `from
tests.conftest import …`-style imports resolving.

**Never add a `tests/__init__.py` under `capas/<name>/tests/`.** It
looks harmless and doesn't fail loudly — it silently grafts the capa's
tests into `backend/tests`' own namespace by basename, so a *second*
capa with a same-named test file gets its coverage silently swallowed,
green run and all. A repo-wide guard test
(`backend/tests/plugins/test_shipped_examples.py`) fails loudly if one
ever reappears.

**A plain `cd backend && uv run pytest ../capas/<name>/tests` run from
outside `backend/` doesn't load this config** — pytest derives its
rootdir/inifile from the args, and an arg living outside `backend/` walks
up to the repo root, which has no `pyproject.toml` at all. The session
header then prints no `configfile:` line — that missing line is the tell.
Run capa-local tests as:

```bash
cd backend && uv run pytest -c pyproject.toml ../capas/<name>/tests -v
```

or include `backend/tests` in the same invocation's args.

## The `_plugin_path` fixture

`connector/`, `runtime/`, `provider/`, `channel/`, and `mcp_bridge/` are
generic names every capa of that shape reuses, and `sys.modules` is
keyed by **name**, not by filesystem path — so without help, whichever
capa's module lands in the cache first wins for the whole pytest
session, and a second capa's test silently imports the first capa's
code instead of its own. The fix is a **function-scoped, `autouse=True`**
fixture that inserts the capa's own root onto `sys.path` and evicts the
cached module **on both fixture setup and teardown** — real, shipped
example (`capas/gdrive_source/tests/test_connector_registers.py`):

```python
PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _evict() -> None:
    """Drop any cached `connector`/`connector.*` module from sys.modules."""
    for name in [n for n in sys.modules if n == "connector" or n.startswith("connector.")]:
        del sys.modules[name]


@pytest.fixture(autouse=True)
def _plugin_path() -> Iterator[None]:
    sys.path.insert(0, str(PLUGIN_ROOT))
    _evict()
    yield
    sys.path.remove(str(PLUGIN_ROOT))
    _evict()


def test_register_contributes_the_drive_connector() -> None:
    # Function-local, INSIDE the fixture's window.
    from connector.connector import register

    contrib = PluginContributions(plugin_id="gdrive_source")
    register(contrib)
    assert "gdrive" in contrib.connectors
```

For a file at `capas/<name>/tests/test_*.py`, `PLUGIN_ROOT` is
`parents[1]`.

**Why function-scoped, not a module-top prelude:** a module-top prelude
runs at *collection* time. Most capa tests import their capa *inside
the test body*, which resolves at *execution* time, long after collection
has finished — a module-top prelude silently does nothing for those
files. A file whose capa imports are genuinely module-level keeps a
plain `sys.path.insert` + eviction prelude *as well* — the two compose,
they're not alternatives to pick between.

**Why the eviction has to be symmetric (setup *and* teardown):** the
production loader (`loader.py`) only evicts the `sys.modules` entries it
itself introduces during its own import call. If a generic name like
`connector` is still cached from a prior test's teardown, the loader's own
import returns that cached module, its "introduced" set is empty, and it
silently hands back the *wrong* capa's `register` to whatever called it
next. Setup-only eviction is invisible until a second capa sharing the
same folder name gets its own capa-local tests — evict on both ends,
always.

A capa with **two** generic packages (`microsoft365`: `connector` +
`mcp_bridge`) evicts both names in the same fixture, not two separate
fixtures.

**One deliberate variant:** in a large, otherwise capa-free test file
where only one test imports a capa's package inside its body, drop
`autouse=True` and have that one test request `_plugin_path` by name as a
parameter instead — `autouse` would otherwise evict and re-import the
capa once per test in a file that has nothing else to do with it.

**This fixture does not validate `plugin.toml`.** It imports
`connector.connector` directly and never reads the manifest — a typo'd
entry point (`conector.connector:register`) wouldn't be caught by it
alone. Every capa also needs *some* test that asserts the entry-point
literal or drives the real loader through `find_plugin`/`load_plugin`.

## Type-checking

A scoped, per-capa `mypy` invocation — never `mypy_path`, and never more
than one capa sharing a folder name (`runtime/`, say) in the same
invocation, or mypy reports `Duplicate module named 'runtime'`:

```bash
cd backend && uv run mypy ../capas/<name>/<code-folder> ../capas/<name>/tests
```

Real example: `cd backend && uv run mypy ../capas/gdrive_source/connector ../capas/gdrive_source/tests`
→ `Success: no issues found in 3 source files`.

**`cd backend &&` is mandatory before every `ruff`/`ruff format`/`mypy`
command scoped to a capa.** `capas/` has no ruff/mypy config of its
own anywhere above it, so running from the repo root instead of
`backend/` loses `backend/pyproject.toml`'s configuration entirely and
produces spurious errors that look like real ones.

If a broader `mypy` run right after a narrower one reports impossible
errors like "Module X has no attribute Y" on code that hasn't changed,
clear the cache before trusting them: `rm -rf backend/.mypy_cache`. A
narrow-then-wide sequence can leave stale cache entries that fabricate
`attr-defined` errors.

## Next

- [Dependencies & Requirements](dependencies-and-requirements.md) for how
  `plugin_depends` and per-capa `requirements` interact with this test
  setup.
