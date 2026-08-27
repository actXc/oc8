from __future__ import annotations

from oc8.collab.contracts import apply_payload_map, department_emits, department_intake


def test_emits_and_intake_lookup() -> None:
    frame = {
        "emits": ["sales.deal.won"],
        "intakes": [{"handoff_type": "project.kickoff", "route": "team_lead", "gate": "approval"}],
    }
    assert department_emits(frame) == ["sales.deal.won"]
    assert department_emits({}) == []
    hit = department_intake(frame, "project.kickoff")
    assert hit is not None and hit["gate"] == "approval"
    assert department_intake(frame, "nope") is None


def test_payload_map_extracts_dotted_paths() -> None:
    event = {"customer": "Acme", "value_eur": 5000, "nested": {"scope": "big"}}
    out = apply_payload_map(
        {
            "customer": "$.customer",
            "budget": "$.value_eur",
            "scope": "$.nested.scope",
            "missing": "$.x",
        },
        event,
    )
    assert out == {"customer": "Acme", "budget": 5000, "scope": "big", "missing": None}
