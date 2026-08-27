"""The neutral tool-call interpreters: value + focus are driven ENTIRELY by a
plugin-supplied spec. The core makes no monetary/software assumption, so with no
spec there is no value and no focus."""

from __future__ import annotations

from oc8.agent.tool_semantics import describe_focus, extract_value

# ---------------------------------------------------------------- value

def test_no_spec_means_no_value() -> None:
    # The core must not assume any key holds a value.
    assert extract_value({"amount": 5000, "value_eur": 9000}) is None
    assert extract_value({"amount": 5000}, {}) is None


def test_direct_fields_from_the_spec() -> None:
    spec = {"direct_fields": ["value_eur", "amount"]}
    assert extract_value({"value_eur": 3200}, spec) == 3200.0
    assert extract_value({"amount": "€1.234,0".replace(".", "").replace(",", ".")}, spec) == 1234.0
    # Highest wins across declared keys.
    assert extract_value({"value_eur": 100, "amount": 900}, spec) == 900.0


def test_direct_fields_are_found_when_nested() -> None:
    spec = {"direct_fields": ["expected_revenue"]}
    args = {"model": "anything", "values": {"expected_revenue": 4500}}
    assert extract_value(args, spec) == 4500.0


def test_line_items_are_summed_as_price_times_qty() -> None:
    # An x2many command-tuple shape [[0, 0, {vals}], ...], generic to the spec.
    spec = {
        "line_items": {
            "path": ["values", "order_line"],
            "price_field": "price_unit",
            "qty_field": "product_uom_qty",
        }
    }
    args = {
        "model": "anything",
        "values": {
            "order_line": [
                [0, 0, {"price_unit": 500, "product_uom_qty": 4}],
                [0, 0, {"price_unit": 1000, "product_uom_qty": 2}],
            ]
        },
    }
    assert extract_value(args, spec) == 4000.0  # 2000 + 2000


def test_line_items_accept_bare_dicts_and_default_qty() -> None:
    spec = {"line_items": {"path": ["lines"], "price_field": "p", "qty_field": "q"}}
    args = {"lines": [{"p": 1500}, {"p": 250, "q": 2}]}
    assert extract_value(args, spec) == 2000.0  # 1500*1 + 250*2


def test_direct_and_line_items_take_the_max() -> None:
    spec = {
        "direct_fields": ["amount_total"],
        "line_items": {"path": ["values", "lines"], "price_field": "p", "qty_field": "q"},
    }
    args = {"values": {"amount_total": 900, "lines": [{"p": 100, "q": 3}]}}
    assert extract_value(args, spec) == 900.0  # max(900, 300)


# ---------------------------------------------------------------- focus

FOCUS_SPEC = {
    "entity_field": "model",
    "labels": {"crm.lead": "Lead", "sale.order": "Angebot"},
    "id_fields": ["id", "record_id"],
    "name_path": ["values", "name"],
    "verbs": {"create_record": "Erstellt", "update_record": "Bearbeitet"},
    "default_verb": "Nutzt",
    "search_tools": ["search_records"],
    "search_label": "Durchsucht",
}


def test_no_spec_means_no_focus() -> None:
    assert describe_focus("update_record", {"model": "crm.lead", "id": 5}, None) is None


def test_unlabelled_entity_is_not_described() -> None:
    # Only entities the spec labels are surfaced; nothing software-specific leaks.
    assert describe_focus("update_record", {"model": "res.users", "id": 5}, FOCUS_SPEC) is None


def test_focus_by_id() -> None:
    got = describe_focus("update_record", {"model": "crm.lead", "id": 26}, FOCUS_SPEC)
    assert got == "Bearbeitet Lead #26"


def test_focus_by_name_when_no_id() -> None:
    got = describe_focus(
        "create_record", {"model": "sale.order", "values": {"name": "Q-42"}}, FOCUS_SPEC
    )
    assert got == "Erstellt Angebot 'Q-42'"


def test_search_focus() -> None:
    got = describe_focus("search_records", {"model": "crm.lead"}, FOCUS_SPEC)
    assert got == "Durchsucht Lead"


# ------------------------------------------- software whose tools ARE the entity

TOOL_SHAPED = {
    "tool_entities": {"create_issue": "issue", "list_issues": "issue"},
    "id_fields": ["index"],
    "labels": {"issue": "Issue"},
    "verbs": {"create_issue": "Legt an"},
    "search_tools": ["list_issues"],
}


def test_an_entity_can_come_from_the_tool_instead_of_an_argument() -> None:
    """Odoo names the model in every call; an issue tracker has one endpoint per
    kind and repeats it nowhere. A core that only understood the first shape left
    a whole system's work out of the live log."""
    assert describe_focus("create_issue", {"index": 4}, TOOL_SHAPED) == "Legt an Issue #4"


def test_a_search_reads_the_same_way_in_that_shape() -> None:
    assert describe_focus("list_issues", {}, TOOL_SHAPED) == "Durchsucht Issue"


def test_an_unlisted_tool_is_still_silent() -> None:
    """Only what a plugin names is surfaced -- the core invents no labels."""
    assert describe_focus("merge_pr", {"index": 4}, TOOL_SHAPED) is None


def test_an_argument_entity_still_wins_where_both_exist() -> None:
    spec = {**TOOL_SHAPED, "entity_field": "model", "labels": {"issue": "Issue", "x": "X"}}
    assert describe_focus("create_issue", {"model": "x", "index": 1}, spec) == "Legt an X #1"
