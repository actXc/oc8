# backend/src/oc8/observability/logs.py
"""Stdlib logging setup for every oc8 process.

Until this existed, nothing in the backend ever attached a handler: no
`basicConfig`, no `dictConfig`. Uvicorn configures its OWN loggers, so requests
were visible, but every `logging.getLogger(__name__)` in `oc8.*` propagated to a
root logger with no handler and was discarded. The cost showed up the first time
something failed in a way tests could not reach: a live approval-path defect had
to be reconstructed from `docker events`, `docker inspect` and the audit table,
while the warnings written specifically to explain it went nowhere.

Deliberately small. Structured/JSON logging and log shipping belong with the OTel
work in this package; this is the part whose absence makes a production failure
undiagnosable.
"""

from __future__ import annotations

import logging
import sys

from oc8.config import Settings

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging(settings: Settings, *, root: logging.Logger | None = None) -> None:
    """Give the root logger a handler, once.

    Idempotent and deferential: if anything has already configured root -- the
    host's uvicorn config, a test runner's capture, an operator's own dictConfig --
    it is left alone, because a second handler prints every line twice and that is
    worse than the format not being ours.

    `root` exists so the tests can watch this happen to a logger of their own:
    pytest's logging plugin owns the real root's handlers during a test, so
    asserting on them there would measure pytest instead of this function.
    """
    root = root if root is not None else logging.getLogger()
    # An unknown level name falls back to INFO rather than raising or silencing:
    # a typo in the environment must not be the reason nobody hears about the
    # next production failure.
    level = logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO)
    root.setLevel(level)
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(handler)
