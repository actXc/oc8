from __future__ import annotations

import uuid

from oc8.realtime.envelope import build_envelope


def test_envelope_has_cloudevents_shape() -> None:
    tid = uuid.uuid4()
    env = build_envelope(
        tid, "run.status", {"run_id": "r1", "state": "running"}, source="oc8/run/r1"
    )
    assert env["specversion"] == "1.0"
    assert env["type"] == "run.status"
    assert env["tenantid"] == str(tid)
    assert env["source"] == "oc8/run/r1"
    assert env["data"] == {"run_id": "r1", "state": "running"}
    # id is a stringified uuid; time is an ISO-8601 string
    assert isinstance(env["id"], str) and uuid.UUID(env["id"])
    assert isinstance(env["time"], str) and "T" in env["time"]


def test_envelope_ids_are_unique() -> None:
    tid = uuid.uuid4()
    a = build_envelope(tid, "x", {}, source="s")
    b = build_envelope(tid, "x", {}, source="s")
    assert a["id"] != b["id"]
