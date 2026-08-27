from __future__ import annotations

import uuid

from cli_harness.park import POLL_INTERVAL_S, is_parked

from oc8 import models as m


def _run(context: dict) -> m.AgentRun:
    return m.AgentRun(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        state="running",
        context=context,
    )


def test_not_parked_with_no_isolated_result() -> None:
    assert is_parked(_run({})) == (False, "")


def test_parked_for_approval() -> None:
    run = _run({"isolated_result": {"status": "waiting_for_approval", "output": "why"}})
    assert is_parked(run) == (True, "waiting_for_approval")


def test_parked_for_input() -> None:
    run = _run({"isolated_result": {"status": "waiting_for_input", "output": "which one?"}})
    assert is_parked(run) == (True, "waiting_for_input")


def test_not_parked_when_isolated_result_is_some_other_shape() -> None:
    run = _run({"isolated_result": {"status": "done", "output": "x"}})
    assert is_parked(run) == (False, "")


def test_poll_interval_matches_nanoclaws_own_constant() -> None:
    # nanoclaw_runtime/runtime/runtime.py's _POLL_SECONDS = 2.0 -- kept in step so an
    # operator's approval isn't outlived by one poller and not the other.
    assert POLL_INTERVAL_S == 2.0
