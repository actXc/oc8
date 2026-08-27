"""Every writer of a `role` row must say which population it is writing.

`role` holds two populations -- people and agents -- and `kind` is the whole of
what separates them. The column defaults to `'human'`, which is the safe default
(a row that defaults wrong grants an AGENT nothing, rather than granting a
person's role every tool right there is), but a default is not a decision. Both
writers in this tree build all five built-in roles in a `for` loop with no
per-name discrimination, and `_seed_acme` then points EVERY agent's `role_id` at
one of those rows: a loop that silently took the default would seed an
`agent_default` row of the wrong kind, and the moment the PDP reads the column
that is every seeded agent denied every tool call, with nothing in the logs
mentioning a role.

**Source-level, and deliberately so** -- the same reason
`tests/seed/test_seeded_approvals_carry_a_department.py` is: running the seed
writes an organization with the hardcoded slug `acme`, which is globally unique,
so `_seed_acme` can be executed exactly once per database and a second test that
calls it fails on the ORGANIZATION insert without ever reaching the assertion it
was written to make.

The behaviour this stands in for is asserted where it can be:
`tests/tenants/test_provision.py` runs the real `create_tenant` and counts the
kinds, and `test_the_catalogue_is_partitioned` pins `role_kind` itself.
"""

from __future__ import annotations

import ast
import pathlib

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "oc8"


def _role_constructions() -> list[tuple[str, int, set[str]]]:
    """Every `m.Role(...)` / `models.Role(...)` call in the tree, with its
    keyword names. AST rather than a grep, so a call split over six lines by the
    formatter is found exactly like a one-liner."""
    found: list[tuple[str, int, set[str]]] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "Role":
                found.append(
                    (
                        str(path.relative_to(_SRC.parent.parent)),
                        node.lineno,
                        {kw.arg for kw in node.keywords if kw.arg},
                    )
                )
    return found


def test_the_sweep_actually_finds_the_role_writers() -> None:
    """A sweep that matched nothing would pass the test below it forever."""
    calls = _role_constructions()
    assert len(calls) >= 3, f"only found {len(calls)} `Role(...)` constructions: {calls}"


def test_every_role_row_written_in_this_tree_names_its_kind() -> None:
    """The forcing function for the next writer.

    A new caller that constructs a `role` row without `kind=` fails here rather
    than in production, where the failure is an agent that stopped working for
    a reason no log line mentions.
    """
    silent = [
        f"{path}:{line}" for path, line, kwargs in _role_constructions() if "kind" not in kwargs
    ]
    assert not silent, (
        "these write a `role` row without saying whether it is a person's or an "
        f"agent's, and take whichever default the column happens to have: {silent}"
    )
