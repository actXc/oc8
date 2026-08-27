from __future__ import annotations

from oc8.authz.pdp import ToolPolicy


def test_connection_id_round_trips_through_json() -> None:
    policy = ToolPolicy(
        enabled=True, read=True, connection_id="11111111-1111-1111-1111-111111111111"
    )
    data = policy.to_json()
    assert data["connection_id"] == "11111111-1111-1111-1111-111111111111"
    restored = ToolPolicy.from_json(data)
    assert restored.connection_id == "11111111-1111-1111-1111-111111111111"


def test_connection_id_defaults_to_none() -> None:
    policy = ToolPolicy.from_json({"enabled": True, "read": True})
    assert policy.connection_id is None
    assert policy.to_json()["connection_id"] is None
