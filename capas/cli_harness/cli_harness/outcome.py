"""One shared way to say "the harness exited cleanly but told us nothing".

`docker_driver.py`'s `logs()` returns a fixed 4000-char TRAILING WINDOW, not
the full log. `tail.py`'s overlap diffing recovers a window that SLIDES, but
nothing can recover content that fell off the front entirely between two
polls -- `_overlap_length` returns 0 and the gap is lost silently. Every one
of these plugins decides done-vs-failed on whether it observed ONE terminal
event (Claude Code's `result`, Codex's `item.completed`/`agent_message`,
opencode's accumulated `text` parts), so a chatty run losing that one line to
the window looks exactly like a task that genuinely failed.

Widening the window is out of scope here -- `docker_driver.logs()` is a
shared sandbox primitive every runtime in this codebase uses, nanoclaw
included. What IS in scope is not lying about which failure happened: a
zero exit code with no terminal event is a DIAGNOSABLE case with its own
message, so an operator reading production logs can tell "the CLI failed the
task" from "we lost the CLI's answer".
"""

from __future__ import annotations


def no_terminal_event(harness: str) -> str:
    return (
        f"{harness} exited cleanly (0) but no terminal event was observed in its "
        "output -- most likely lost to the sandbox driver's 4000-char trailing "
        "log window rather than an actual task failure. Re-run to retry."
    )
