"""AgentRun lifecycle states and the allowed transition graph."""

from __future__ import annotations

from enum import StrEnum


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    FAILED = "failed"
    DONE = "done"
    INTERRUPTED = "interrupted"


TERMINAL: frozenset[RunState] = frozenset(
    {RunState.DONE, RunState.FAILED, RunState.INTERRUPTED}
)

_ALLOWED: dict[RunState, frozenset[RunState]] = {
    RunState.QUEUED: frozenset({RunState.RUNNING, RunState.FAILED, RunState.INTERRUPTED}),
    RunState.RUNNING: frozenset(
        {
            RunState.DONE,
            RunState.FAILED,
            RunState.WAITING_FOR_APPROVAL,
            RunState.WAITING_FOR_INPUT,
            RunState.INTERRUPTED,
        }
    ),
    # A suspended run resumes by being re-queued (QUEUED) so the worker picks it
    # up again, or can be resumed inline (RUNNING) / abandoned (FAILED).
    RunState.WAITING_FOR_APPROVAL: frozenset({RunState.QUEUED, RunState.RUNNING, RunState.FAILED}),
    RunState.WAITING_FOR_INPUT: frozenset({RunState.QUEUED, RunState.RUNNING, RunState.FAILED}),
    RunState.DONE: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.INTERRUPTED: frozenset(),
}


class InvalidTransition(ValueError):
    """Raised when a run is moved between states the machine forbids."""


def can_transition(src: RunState, dst: RunState) -> bool:
    return dst in _ALLOWED[src]


def assert_transition(src: RunState, dst: RunState) -> None:
    if not can_transition(src, dst):
        raise InvalidTransition(f"{src.value} -> {dst.value} is not allowed")
