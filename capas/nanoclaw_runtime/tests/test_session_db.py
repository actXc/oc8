"""The session-DB protocol, from oc8's side.

The parity rule is the sharp edge: nothing in the schema enforces that the host
writes even `seq` and the container odd, only two helper functions do -- and the
agent-facing message id IS the seq, so a collision breaks message editing.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import pytest

# capas/nanoclaw_runtime/tests/test_session_db.py -> parents[1] is the plugin root,
# which is the directory `runtime/` is imported from.
# See test_messages.py for why this is per-file rather than pytest's `pythonpath`.
# Module-top form, because this file's plugin import is module-level (collection time).
PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _evict() -> None:
    """Drop every cached `runtime`/`runtime.*` module from sys.modules.

    `runtime` is the package name EVERY `runtime_adapter` plugin now ships --
    four plugins behind one top-level module name (design §2) -- and sys.modules
    is keyed by NAME, not by path. Called SYMMETRICALLY, before this module's
    import AND after it. See test_messages.py's copy for the full reasoning; the
    trailing half is the load-bearing one.
    """
    for _stale in [n for n in sys.modules if n == "runtime" or n.startswith("runtime.")]:
        del sys.modules[_stale]


_evict()
sys.path.insert(0, str(PLUGIN_ROOT))

from runtime.session_db import (  # noqa: E402
    CHANNEL_TYPE,
    ROUTE_NAME,
    ack_status,
    heartbeat_age_s,
    next_even_seq,
    read_messages_out,
    write_message_in,
)

# Symmetric with the insert above -- nothing generic may be left cached for the
# next runtime plugin's test file (or for `loader.import_entry_point`).
_evict()
sys.path.remove(str(PLUGIN_ROOT))

# Mirrors only the columns oc8 touches. The REAL schema comes from nanoclaw --
# see test_schema_contract.py, which is what guards this fixture against drift.
_INBOUND = """
CREATE TABLE messages_in (
  id TEXT PRIMARY KEY, seq INTEGER UNIQUE, kind TEXT NOT NULL, timestamp TEXT NOT NULL,
  status TEXT DEFAULT 'pending', process_after TEXT, recurrence TEXT, series_id TEXT,
  tries INTEGER DEFAULT 0, trigger INTEGER NOT NULL DEFAULT 1, platform_id TEXT,
  channel_type TEXT, thread_id TEXT, content TEXT NOT NULL, source_session_id TEXT,
  on_wake INTEGER NOT NULL DEFAULT 0);
"""
_OUTBOUND = """
CREATE TABLE messages_out (
  id TEXT PRIMARY KEY, seq INTEGER UNIQUE, in_reply_to TEXT, timestamp TEXT NOT NULL,
  deliver_after TEXT, recurrence TEXT, kind TEXT NOT NULL, platform_id TEXT,
  channel_type TEXT, thread_id TEXT, content TEXT NOT NULL);
CREATE TABLE processing_ack (
  message_id TEXT PRIMARY KEY, status TEXT NOT NULL, status_changed TEXT NOT NULL);
"""


@pytest.fixture()
def session(tmp_path: Path) -> str:
    sqlite3.connect(tmp_path / "inbound.db").executescript(_INBOUND)
    sqlite3.connect(tmp_path / "outbound.db").executescript(_OUTBOUND)
    return str(tmp_path)


def test_the_first_host_message_gets_seq_two(session: str) -> None:
    assert next_even_seq(session) == 2


def test_seq_advances_past_the_containers_high_water_mark(session: str) -> None:
    """host=2, container=7: a scan that reads only messages_in would stop at 2
    and answer 4 here, same as a correct scan would for a much lower container
    seq -- this case is what actually tells the two implementations apart."""
    sqlite3.connect(f"{session}/inbound.db").execute(
        "INSERT INTO messages_in (id, seq, kind, timestamp, content) "
        "VALUES ('h', 2, 'chat', 't', '{}')"
    ).connection.commit()
    sqlite3.connect(f"{session}/outbound.db").execute(
        "INSERT INTO messages_out (id, seq, timestamp, kind, content) "
        "VALUES ('c', 7, 't', 'chat', '{}')"
    ).connection.commit()

    assert next_even_seq(session) == 8


def test_seq_advances_past_the_hosts_own_high_water_mark(session: str) -> None:
    """host=10, container=3: the mirror case -- a scan that reads only
    messages_out would stop at 3 and answer 4 here."""
    sqlite3.connect(f"{session}/inbound.db").execute(
        "INSERT INTO messages_in (id, seq, kind, timestamp, content) "
        "VALUES ('h', 10, 'chat', 't', '{}')"
    ).connection.commit()
    sqlite3.connect(f"{session}/outbound.db").execute(
        "INSERT INTO messages_out (id, seq, timestamp, kind, content) "
        "VALUES ('c', 3, 't', 'chat', '{}')"
    ).connection.commit()

    assert next_even_seq(session) == 12


def test_a_task_is_written_as_a_pending_chat_message(session: str) -> None:
    message_id = write_message_in(session, text="Erstelle ein Angebot")

    row = (
        sqlite3.connect(f"{session}/inbound.db")
        .execute(
            "SELECT kind, status, trigger, content FROM messages_in WHERE id = ?", (message_id,)
        )
        .fetchone()
    )

    assert row[0] == "chat"
    assert row[1] == "pending"
    assert row[2] == 1, "trigger=0 would file the task as context and never wake the agent"
    assert json.loads(row[3])["text"] == "Erstelle ein Angebot"


def test_only_newer_outbound_rows_are_returned(session: str) -> None:
    conn = sqlite3.connect(f"{session}/outbound.db")
    for seq, text in ((1, "erst"), (3, "dann")):
        conn.execute(
            "INSERT INTO messages_out (id, seq, timestamp, kind, content) "
            "VALUES (?, ?, 't', 'chat', ?)",
            (f"m{seq}", seq, json.dumps({"text": text})),
        )
    conn.commit()

    rows = read_messages_out(session, after_seq=1)

    assert [r.seq for r in rows] == [3]
    assert json.loads(rows[0].content)["text"] == "dann"


def test_an_unacked_message_has_no_status(session: str) -> None:
    assert ack_status(session, "nope") is None


def test_a_missing_heartbeat_is_reported_as_such(session: str) -> None:
    assert heartbeat_age_s(session) is None


def test_repeated_calls_do_not_leak_open_connections(session: str) -> None:
    """Regression guard: `with conn:` on a sqlite3.Connection only commits or
    rolls back on exit, it does NOT close the connection -- so a bare
    `with _connect(...) as conn:` leaks one fd per call (three per
    write_message_in call, since it used to open a write connection and then
    call next_even_seq, which opens two read-only ones of its own). A later
    task polls these functions every couple of seconds for a run's whole
    lifetime, so an unbounded fd count here is a real, not theoretical, leak.
    """

    def open_fds() -> int:
        return len(os.listdir("/dev/fd"))

    write_message_in(session, text="warmup")  # settle any one-time fd cost
    baseline = open_fds()

    for _ in range(200):
        write_message_in(session, text="tick")
        next_even_seq(session)
        read_messages_out(session, after_seq=0)
        ack_status(session, "nope")

    assert open_fds() <= baseline + 5, "fd count grew with call volume -- a connection leak"


# ------------------------------------------------- routing, learned the hard way


def test_a_message_carries_the_routing_the_harness_resolves_from(session: str) -> None:
    """The harness renders an inbound message's `from="..."` by looking up
    (channel_type, platform_id) in the destinations table. With both NULL the
    lookup misses, the model has to guess the destination name off the visible
    sender, and its answer is dropped as an unknown destination -- while the batch
    is still acked `completed`. Observed live 2026-07-27: a run that "succeeded"
    with no output at all."""
    run_id = uuid.uuid4()

    message_id = write_message_in(
        session, text="Wie viele Leads?", channel_type=CHANNEL_TYPE, platform_id=str(run_id)
    )

    row = (
        sqlite3.connect(f"{session}/inbound.db")
        .execute("SELECT channel_type, platform_id FROM messages_in WHERE id = ?", (message_id,))
        .fetchone()
    )
    assert row == (CHANNEL_TYPE, str(run_id))


def test_the_sender_is_the_destination_name_not_a_second_word_for_it(session: str) -> None:
    """The model sees the sender and answers to it. If the sender says one word
    and the destinations table another, the reply is addressed to a name that
    does not exist and is dropped."""
    message_id = write_message_in(session, text="hallo")

    content = (
        sqlite3.connect(f"{session}/inbound.db")
        .execute("SELECT content FROM messages_in WHERE id = ?", (message_id,))
        .fetchone()[0]
    )
    assert json.loads(content)["sender"] == ROUTE_NAME


# ------------------------------------------------ heartbeat, learned the same way


def test_a_heartbeat_from_an_earlier_run_is_not_a_reading(session: str) -> None:
    """A `.heartbeat` older than this run says nothing about THIS container --
    and treating it as a reading is worse than having none: it is instantly
    "stale", so the caller's watchdog kills a healthy run seconds after it starts.
    Observed live: a run failed as "the agent container stopped responding" 5s in,
    reporting an age of 942s."""
    path = os.path.join(session, ".heartbeat")
    open(path, "w").close()
    stale = time.time() - 900
    os.utime(path, (stale, stale))
    run_started = time.time()

    assert heartbeat_age_s(session, since=run_started) is None
    assert heartbeat_age_s(session) is not None, "without `since` it is still a reading"


def test_a_heartbeat_written_during_the_run_is_a_reading(session: str) -> None:
    run_started = time.time() - 10
    open(os.path.join(session, ".heartbeat"), "w").close()

    age = heartbeat_age_s(session, since=run_started)

    assert age is not None and age < 5
