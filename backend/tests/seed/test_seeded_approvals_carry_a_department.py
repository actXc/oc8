"""A seeded approval must be filed the same way a raised one is.

`raise_approval` derives `department_id` from the agent, but the demo seed
builds its `ApprovalRequest` rows by hand -- it needs deterministic ids and must
not announce anything on a messenger, so it cannot go through the funnel. That
leaves it as the one place in the tree where an approval can be born with
`department_id IS NULL`, which means TENANT-WIDE and is visible only to the
unrestricted.

The cost of getting that wrong is not an error. It is a seeded tenant where a
seat-holding Head of Sales opens the one screen this slice exists to build and
finds it empty, while an org_admin looking at the same tenant sees five pending
approvals and concludes the screen works.

Source-level, and deliberately so: running the seed writes to the fixed
`ACME_TENANT_ID`, which has no per-test rollback.
"""

from __future__ import annotations

import ast
import pathlib

from oc8.seed import AGENTS, ESCALATIONS

_SEED = pathlib.Path(__file__).resolve().parents[2] / "src" / "oc8" / "seed" / "__init__.py"

#: Index of `dept_slug` in an AGENTS row, per the unpacking at `seed/__init__.py`.
_DEPT_SLUG = 13


def test_the_seed_files_its_approvals_under_a_department() -> None:
    tree = ast.parse(_SEED.read_text(), filename=str(_SEED))
    built = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "ApprovalRequest"
    ]
    assert built, (
        "no ApprovalRequest construction found in the seed -- this sweep has gone "
        "blind and would pass for ever"
    )
    for call in built:
        assert any(kw.arg == "department_id" for kw in call.keywords), (
            f"seed/__init__.py:{call.lineno} builds an ApprovalRequest with no "
            f"department_id, so it is born company-wide and no seat-holder sees it"
        )


def test_every_seeded_escalation_names_an_agent_the_seed_actually_creates() -> None:
    """The lookup that files them is a dict access, so a typo is a KeyError.

    Better here, where it names the slug, than at `oc8 seed` -- which would fail
    half-way through a tenant and leave it partly built.
    """
    departments = {row[0]: row[_DEPT_SLUG] for row in AGENTS}
    for escalation in ESCALATIONS:
        agent_slug = escalation[1]
        assert agent_slug in departments, (
            f"ESCALATIONS names agent {agent_slug!r}, which AGENTS does not create"
        )
        assert departments[agent_slug], f"agent {agent_slug!r} has no department slug"


def test_the_agents_row_layout_this_file_depends_on_has_not_moved() -> None:
    """`_DEPT_SLUG` is a positional index into a 15-tuple.

    If a column is inserted before it, the test above starts reading `avatar` and
    keeps passing while asserting nothing -- so the shape is pinned here, next to
    the assumption.
    """
    assert all(len(row) == 15 for row in AGENTS)
    for row in AGENTS:
        assert isinstance(row[_DEPT_SLUG], str) and not row[_DEPT_SLUG].startswith("oklch"), (
            "the dept_slug column has moved; _DEPT_SLUG is reading the avatar colour"
        )
