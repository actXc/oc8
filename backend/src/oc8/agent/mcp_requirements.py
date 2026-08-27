from __future__ import annotations

from typing import Any


def wrap_with_requirements(
    command: str, args: list[str], cfg: dict[str, Any]
) -> tuple[str, list[str]]:
    """Wrap an MCP bridge's launch in `uv run --with` when its connection
    config declares `requirements` (design §10.1), else return them unchanged.

    Pure and total: no subprocess, no filesystem, no `McpSession`. The manifest
    stays declarative -- an author lists PEP 508 strings, and THIS is the only
    place that decides how they are made available, so changing the isolation
    mechanism later touches one function and no plugin manifest.

    `uv` grammar verified against the actually-installed **uv 0.11.15**
    (`-w, --with <WITH>`, "Run with the given packages installed"):

    | shape                                                            | result |
    |-------------------------------------------------------------------|--------|
    | `uv run --with "packaging>=24" --with iniconfig -- python -c ...` | works |
    | `uv run --with "packaging>=24,iniconfig" -- python -c ...`        | works (comma-joined) |
    | `uv run --with iniconfig python -c ...` (no `--`)                 | works (`--` optional) |

    Repeated flags plus an explicit `--` is the canonical shape used here:
    unambiguous under all three, and the `--` means a requirement string can
    never be mistaken for the wrapped command.
    """
    reqs = cfg.get("requirements") or []
    if not reqs:
        return command, args
    flags = [flag for req in reqs for flag in ("--with", req)]
    return "uv", ["run", *flags, "--", command, *args]
