"""Regression guard: the seeded finance/sales/hr skills must actually be
assignable to an agent in the department they're seeded for -- i.e.
`authz.pdp.missing_skill_requirements` must report no missing *tool*
requirements against the real department frame.

This mirrors exactly what the seeder wires together (`oc8.seed`):

  - `SKILL_CATEGORY_TOOL_FRAMES[category]` becomes a seeded skill version's
    `definition["requires"]["tools"]`.
  - `DEPT_FRAMES[dept_slug]`, via `_frame_json`, becomes the seeded
    department's `frame`.

If either dict drifts (a category's required tools stop matching what the
mapped department's frame actually enables), this test fails -- which is the
whole point: it's the regression that let a seeded skill 422 on assignment.
"""

from __future__ import annotations

import uuid

import pytest

from oc8.authz.pdp import missing_skill_requirements
from oc8.seed import DEPT_FRAMES, SKILL_CATEGORY_TOOL_FRAMES, _frame_json

# category -> the department slug the seeder actually assigns that category's
# agents/skills to (DEPARTMENTS / AGENTS in oc8.seed). Confirmed against
# oc8.seed.DEPT_FRAMES: "buchhaltung", "vertrieb", and "hr" all exist there.
CATEGORY_TO_DEPT_SLUG: dict[str, str] = {
    "finance": "buchhaltung",
    "sales": "vertrieb",
    "hr": "hr",
}


@pytest.mark.parametrize("category", sorted(CATEGORY_TO_DEPT_SLUG))
def test_seeded_category_skill_is_assignable_in_its_department(category: str) -> None:
    dept_slug = CATEGORY_TO_DEPT_SLUG[category]
    assert dept_slug in DEPT_FRAMES, f"{dept_slug!r} missing from DEPT_FRAMES"

    required_tools = SKILL_CATEGORY_TOOL_FRAMES[category]

    # Build the frame exactly as the seeder does for this department (see
    # oc8.seed._seed_acme): _frame_json(tid, dept_slug, kb_ids). tid/kb_ids
    # only affect the "kbs" list, which this test doesn't exercise.
    tid = uuid.uuid4()
    frame = _frame_json(tid, dept_slug, [])

    # A freshly-assigned agent has no narrowing yet, and this guard is about
    # tool requirements, not KBs -- granted_kb_ids=None per spec.
    requires = {"tools": required_tools, "kbs": []}
    missing = missing_skill_requirements(frame, {}, requires, granted_kb_ids=None)

    missing_tools = [x for x in missing if x.kind == "tool"]
    assert missing_tools == [], (
        f"seeded '{category}' skill requires {required_tools} but department "
        f"'{dept_slug}' frame does not satisfy: {missing_tools}"
    )


def test_category_to_dept_frames_do_not_drift_apart() -> None:
    # If a category in SKILL_CATEGORY_TOOL_FRAMES has no corresponding entry
    # here (or the mapped dept slug vanishes from DEPT_FRAMES), the seeder's
    # skill<->department wiring has drifted and this must fail loudly rather
    # than silently skip coverage.
    for category in CATEGORY_TO_DEPT_SLUG:
        assert category in SKILL_CATEGORY_TOOL_FRAMES
    for dept_slug in CATEGORY_TO_DEPT_SLUG.values():
        assert dept_slug in DEPT_FRAMES
