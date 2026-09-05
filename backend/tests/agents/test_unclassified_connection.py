"""A connection with no read/write classification has to say so.

`required_right` fail-closes an unlisted tool to `write`. For authorization that
is correct. For idempotency it silently inverts the rule that reads are never
replayed: every read gets cached, and a second identical search inside one task
comes back as the FIRST one's result with a note saying no second action was
taken -- the agent is told the world has not changed.

Found live on 2026-07-30: a connection created before its plugin declared its
scopes kept `[]` afterwards, because a plugin manifest does not reach rows that
already exist. Its reads were still being cached three days later, and nothing
anywhere said so. This is that missing sentence.
"""

from __future__ import annotations

import logging
import uuid

from oc8.agent import tool_idempotency
from oc8.agent.tool_idempotency import warn_unclassified_connection
from oc8.authz.pdp import required_right


def test_an_undeclared_tool_counts_as_a_write() -> None:
    """The behaviour the warning is about, stated so it cannot drift silently."""
    assert required_right("search_records", None) == "modify"
    assert required_right("search_records", {}) == "modify"
    # An empty LIST is what the live row actually held -- not a dict, so the
    # gateway reads it as "no classification" rather than "no read tools".
    assert required_right("search_records", []) == "modify"  # type: ignore[arg-type]


def test_a_declared_read_stays_a_read() -> None:
    scopes = {"read": ["search_records"], "send": ["update_record"]}
    assert required_right("search_records", scopes) == "read"
    assert required_right("update_record", scopes) == "modify"


def test_the_warning_names_the_connection_and_the_consequence(
    caplog: object,
) -> None:
    tool_idempotency._UNCLASSIFIED.clear()
    conn_id = uuid.uuid4()

    with caplog.at_level(logging.WARNING, logger="oc8.agent.tool_idempotency"):  # type: ignore[attr-defined]
        warn_unclassified_connection(conn_id, "gitea")

    message = caplog.text  # type: ignore[attr-defined]
    assert "gitea" in message
    assert str(conn_id) in message
    # The operator has to be able to act on it without reading the source.
    assert "replayed" in message
    assert "Re-apply" in message


def test_it_complains_once_per_connection_not_once_per_call(
    caplog: object,
) -> None:
    """A busy agent makes thousands of calls on one connection; a warning per
    call would bury the one line that matters."""
    tool_idempotency._UNCLASSIFIED.clear()
    conn_id = uuid.uuid4()

    with caplog.at_level(logging.WARNING, logger="oc8.agent.tool_idempotency"):  # type: ignore[attr-defined]
        for _ in range(5):
            warn_unclassified_connection(conn_id, "gitea")
        warn_unclassified_connection(uuid.uuid4(), "odoo")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]  # type: ignore[attr-defined]
    assert len(warnings) == 2
