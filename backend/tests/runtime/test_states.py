from __future__ import annotations

import pytest

from oc8.runtime.states import (
    InvalidTransition,
    RunState,
    assert_transition,
    can_transition,
)


def test_allowed_transitions() -> None:
    assert can_transition(RunState.QUEUED, RunState.RUNNING)
    assert can_transition(RunState.RUNNING, RunState.DONE)
    assert can_transition(RunState.RUNNING, RunState.WAITING_FOR_APPROVAL)
    assert can_transition(RunState.WAITING_FOR_APPROVAL, RunState.RUNNING)


def test_forbidden_transitions() -> None:
    assert not can_transition(RunState.DONE, RunState.RUNNING)
    assert not can_transition(RunState.QUEUED, RunState.DONE)


def test_assert_transition_raises() -> None:
    with pytest.raises(InvalidTransition):
        assert_transition(RunState.DONE, RunState.RUNNING)


def test_interrupted_is_terminal() -> None:
    from oc8.runtime.states import TERMINAL, RunState, can_transition

    assert RunState.INTERRUPTED in TERMINAL
    for dst in RunState:
        assert not can_transition(RunState.INTERRUPTED, dst)


def test_queued_and_running_can_be_interrupted() -> None:
    from oc8.runtime.states import RunState, can_transition

    assert can_transition(RunState.QUEUED, RunState.INTERRUPTED)
    assert can_transition(RunState.RUNNING, RunState.INTERRUPTED)


def test_a_suspended_run_cannot_jump_straight_to_interrupted() -> None:
    # waiting_for_* runs are flagged, not transitioned, by cancel -- so the
    # machine deliberately does NOT allow waiting_for_* -> interrupted directly.
    from oc8.runtime.states import RunState, can_transition

    assert not can_transition(RunState.WAITING_FOR_APPROVAL, RunState.INTERRUPTED)
    assert not can_transition(RunState.WAITING_FOR_INPUT, RunState.INTERRUPTED)
