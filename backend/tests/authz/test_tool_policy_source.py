"""`tool_policy_source` -- the guardrails UX Source column (design: OC8
Guardrails UX spec §"Source"). Walks agent -> department -> capa_default,
narrowest first, as an ordered list rather than a hardcoded pair, so a future
`company` tier can be inserted without touching call sites (see the
function's own docstring and `PROVENANCE_LEVELS`).
"""

from __future__ import annotations

from oc8.authz.pdp import tool_policy_source


def _frame(**tools: dict[str, object]) -> dict[str, object]:
    return {"tools": tools}


def test_a_key_in_narrowing_overridden_keys_is_agent_sourced_even_if_frame_also_differs() -> None:
    frame = _frame(post_message={"enabled": True, "modify": True})
    defaults = _frame(post_message={"enabled": True, "modify": False})
    assert (
        tool_policy_source(
            "post_message",
            frame=frame,
            capa_defaults=defaults,
            narrowing_overridden_keys=frozenset({"post_message"}),
        )
        == "agent"
    )


def test_a_key_differing_from_its_capa_default_is_department_sourced() -> None:
    frame = _frame(create_record={"enabled": True, "modify": True})
    defaults = _frame(create_record={"enabled": True, "modify": False})
    assert (
        tool_policy_source(
            "create_record",
            frame=frame,
            capa_defaults=defaults,
            narrowing_overridden_keys=frozenset(),
        )
        == "department"
    )


def test_a_key_matching_its_capa_default_is_capa_default_sourced() -> None:
    entry = {"enabled": True, "modify": False}
    frame = _frame(search_records=dict(entry))
    defaults = _frame(search_records=dict(entry))
    assert (
        tool_policy_source(
            "search_records",
            frame=frame,
            capa_defaults=defaults,
            narrowing_overridden_keys=frozenset(),
        )
        == "capa_default"
    )


def test_no_captured_defaults_at_all_reports_department_for_every_non_agent_key() -> None:
    # capa_defaults=None: a hand-created department, or one that predates the
    # column -- see Department.frame_capa_defaults's own docstring. Nothing
    # below "department" is reachable without a captured default to diff
    # against, regardless of what the frame actually contains.
    frame = _frame(get_record={"enabled": True, "read": True})
    assert (
        tool_policy_source(
            "get_record",
            frame=frame,
            capa_defaults=None,
            narrowing_overridden_keys=frozenset(),
        )
        == "department"
    )


def test_a_key_absent_from_both_frame_and_defaults_falls_through_to_capa_default() -> None:
    # An agent-exclusive grant key with no frame counterpart at all: both
    # sides read as missing (None == None), so nothing is "different".
    assert (
        tool_policy_source(
            "list_models",
            frame=_frame(),
            capa_defaults=_frame(),
            narrowing_overridden_keys=frozenset(),
        )
        == "capa_default"
    )
