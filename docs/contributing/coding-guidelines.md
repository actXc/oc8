# Coding Guidelines

## Python

`backend/pyproject.toml` is the source of truth — read it rather than
trusting a summary that can drift. As of this writing:

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "ASYNC", "RUF"]

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]
```

`mypy` runs in **strict mode** across `src` and `tests` alike — a test
file is checked at the same strictness as production code, not exempted.
Any `ruff` ignore or `per-file-ignores` entry in the file carries a
comment explaining *why*; add one if you introduce a new exemption, don't
leave a bare ignore.

Run before committing:

```bash
cd backend
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```

## Testing

Integration-style tests run against real Postgres and Redis via
`testcontainers`, not mocks of the database layer — `backend/tests/conftest.py`'s
`app_session` fixture chain is what most API/DB tests build on. Pure-unit
tests that need neither the database nor the running app don't pay that
cost; see [Testing Capas](../developer/testing-plugins.md) for where
capa tests specifically live and why.

Write the failing test before the implementation. This isn't a style
preference stated for its own sake — a test written after the code it's
supposed to verify tends to assert what the code already does rather than
what it's supposed to do, and misses exactly the edge case the
implementation got wrong. A red-then-green cycle catches that; a
green-only cycle can't.

When you find or fix a real bug, prefer a **regression test that's proven
red against the pre-fix code** over one that's only ever been run green —
if you can't make the test fail by reverting your fix, you don't yet know
it tests the thing you think it does.

## The "grep after touching core" check

Per [Core Architecture](architecture.md#the-microkernel-principle): after
editing anything under `backend/src/oc8/` outside `capas/`, grep your
diff for product names, specific field names, or locale strings that
shouldn't be there. A core file that starts naming a vendor's API shape or
a specific business scenario is a sign the change belongs in a capa
instead — even if it would be more convenient to bolt it onto core right
now.

## Comments

Comments explain **why**, not what — a well-named function or variable
already says what it does. Reserve a comment for a non-obvious constraint,
an invariant that isn't visible from the code around it, or a workaround
for a specific, cited bug. If removing a comment wouldn't confuse the next
reader, it shouldn't be there.

## Scope discipline

A bug fix doesn't need surrounding cleanup; a one-off script doesn't need
a reusable abstraction built around it. Three similar lines are better
than a premature abstraction extracted for a fourth call site that
doesn't exist yet. This mirrors [Keep it Simple](architecture.md#keep-it-simple)
at the level of an individual change, not just the system's overall
shape.

## Next

- [Git Guidelines](git-guidelines.md) for commit and branch conventions.
- [Core Architecture](architecture.md) for where a given change belongs.
