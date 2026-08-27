"""The backend configured no logging handler at all.

Not a cosmetic gap: a live approval-path failure had to be diagnosed from
`docker events`, `docker inspect` and the audit table, because every WARNING the
code emitted -- including the ones written specifically to explain that failure --
propagated to a root logger with no handler and was dropped on the floor.

These tests configure a THROWAWAY logger rather than the real root: pytest's own
logging plugin owns root's handlers during a test, so asserting on them there
would measure pytest, not this module.
"""

from __future__ import annotations

import io
import logging
import uuid

from oc8.config import Settings
from oc8.observability.logs import setup_logging


def _target() -> logging.Logger:
    """A private stand-in for the root logger, with a name no one else uses."""
    logger = logging.getLogger(f"logs-test-{uuid.uuid4().hex}")
    logger.propagate = False
    return logger


def _captured(logger: logging.Logger) -> io.StringIO:
    stream = io.StringIO()
    logger.handlers[0].stream = stream  # type: ignore[attr-defined]
    return stream


def test_a_module_logger_actually_reaches_a_handler() -> None:
    """The symptom, stated as a test: something logs, and it comes out."""
    root = _target()
    setup_logging(Settings(log_level="INFO"), root=root)
    stream = _captured(root)

    logging.getLogger(f"{root.name}.somewhere").warning("the container never came up")

    assert "the container never came up" in stream.getvalue()


def test_the_level_comes_from_configuration() -> None:
    root = _target()
    setup_logging(Settings(log_level="WARNING"), root=root)
    stream = _captured(root)

    logging.getLogger(f"{root.name}.somewhere").info("chatter")
    logging.getLogger(f"{root.name}.somewhere").warning("trouble")

    assert "chatter" not in stream.getvalue()
    assert "trouble" in stream.getvalue()


def test_setting_up_twice_does_not_double_every_line() -> None:
    """Both the API's lifespan and a CLI entrypoint may call it in the same
    process; two handlers would print everything twice."""
    root = _target()

    setup_logging(Settings(), root=root)
    setup_logging(Settings(), root=root)

    assert len(root.handlers) == 1


def test_an_existing_handler_is_left_alone() -> None:
    """Under a host that already configured logging -- uvicorn's own config, a
    test runner's capture, an operator's dictConfig -- adding ours on top would
    duplicate every line."""
    root = _target()
    theirs = logging.StreamHandler()
    root.addHandler(theirs)

    setup_logging(Settings(), root=root)

    assert root.handlers == [theirs]


def test_the_line_says_which_logger_spoke() -> None:
    """A message without its logger name is nearly useless in a system where a
    gateway, a runtime and a worker all warn about the same run."""
    root = _target()
    setup_logging(Settings(log_level="INFO"), root=root)
    stream = _captured(root)

    logging.getLogger(f"{root.name}.runtime.nanoclaw").warning("parked")

    assert f"{root.name}.runtime.nanoclaw" in stream.getvalue()


def test_an_unknown_level_name_does_not_silence_the_process() -> None:
    """A typo in OC8_LOG_LEVEL must not be the reason nobody hears about the
    next production failure."""
    root = _target()

    setup_logging(Settings(log_level="LOUD"), root=root)
    stream = _captured(root)
    logging.getLogger(f"{root.name}.somewhere").info("still audible")

    assert "still audible" in stream.getvalue()
