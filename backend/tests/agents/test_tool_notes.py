"""apply_tool_notes is the neutral seam (mirrors focus_spec/value_spec/
outward_tools) a plugin's tool_pack.toml uses to attach extra guidance to a
specific tool's own description -- core has no idea what the note says or
which software it's about. Exists because live-observed 2026-08-26: a
deterministic model kept guessing the same wrong Odoo field name on every
fresh trigger, since the MCP server's own tool description can't know a
live instance's actual data shape."""

from __future__ import annotations

from oc8.agent.tool_notes import apply_tool_notes
from oc8.modelrouter.types import NeutralTool

_TOOLS = [
    NeutralTool(name="search_records", description="Search records.", parameters={}),
    NeutralTool(name="get_record", description="Get one record.", parameters={}),
]


def test_a_matching_note_is_appended_to_that_tools_description() -> None:
    out = apply_tool_notes(_TOOLS, {"tool_notes": {"search_records": "No sla_date field."}})
    by_name = {t.name: t for t in out}
    assert by_name["search_records"].description == "Search records.\n\nNo sla_date field."
    assert by_name["get_record"].description == "Get one record."


def test_no_tool_notes_key_leaves_tools_unchanged() -> None:
    out = apply_tool_notes(_TOOLS, {})
    assert out == _TOOLS


def test_a_note_for_a_tool_not_in_the_list_is_ignored() -> None:
    out = apply_tool_notes(_TOOLS, {"tool_notes": {"unknown_tool": "irrelevant"}})
    assert [t.description for t in out] == [t.description for t in _TOOLS]


def test_a_non_dict_tool_notes_value_is_ignored_not_raised() -> None:
    out = apply_tool_notes(_TOOLS, {"tool_notes": "not a dict"})
    assert out == _TOOLS


def test_parameters_pass_through_unchanged() -> None:
    tools = [NeutralTool(name="search_records", description="d", parameters={"type": "object"})]
    out = apply_tool_notes(tools, {"tool_notes": {"search_records": "note"}})
    assert out[0].parameters == {"type": "object"}
