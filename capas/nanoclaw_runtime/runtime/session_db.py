"""oc8's half of nanoclaw's session-DB protocol (docs/db-session.md).

Only INSERT/SELECT on documented columns -- the schema itself is nanoclaw's, and
a contract test pins that assumption to the real image.

The parity rule matters more than it looks: `seq` is the agent-facing message id,
the host writes even values and the container odd ones, and NOTHING in the schema
enforces it. Two rows with the same seq would break message editing, so the next
value is always computed across BOTH tables.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time
import uuid
from contextlib import closing
from typing import NamedTuple

#: The single name for "the oc8 operator" on both legs of the round trip: the
#: `destinations` row the agent may answer to, the sender it sees on an inbound
#: message, and the name the delivery instruction tells it to address. The
#: harness matches the `<message to="...">` target against the destinations
#: table by exact name, so a second word for the same party is a dropped answer.
ROUTE_NAME = "operator"
#: nanoclaw's `channel_type` for this host. Paired with the run id as
#: `platform_id`, it is what `findByRouting` resolves back to ROUTE_NAME.
CHANNEL_TYPE = "oc8"


class OutRow(NamedTuple):
    seq: int
    kind: str
    content: str
    timestamp: str


def _connect(path: str, *, readonly: bool = False) -> sqlite3.Connection:
    uri = f"file:{path}?mode={'ro' if readonly else 'rw'}"
    return sqlite3.connect(uri, uri=True, timeout=5.0)


def _now() -> str:
    return dt.datetime.now(tz=dt.UTC).isoformat()


def next_even_seq(session: str) -> int:
    """Next even seq, past the highest value in EITHER table.

    Note: `sqlite3.Connection` used as a context manager only commits or rolls
    back on exit -- it does NOT close the connection or its file descriptor.
    `closing()` is what actually closes it, so every connect() here is wrapped
    in it (nesting `with conn:` inside for the commit/rollback semantics where
    a write happens).
    """
    highest = 0
    for name, table in (("inbound.db", "messages_in"), ("outbound.db", "messages_out")):
        path = os.path.join(session, name)
        if not os.path.exists(path):
            continue
        with closing(_connect(path, readonly=True)) as conn:
            # `table` is one of two fixed literals above, never user input.
            row = conn.execute(f"SELECT MAX(seq) FROM {table}").fetchone()
        highest = max(highest, int(row[0] or 0))
    return highest + 2 if highest % 2 == 0 else highest + 1


def write_message_in(
    session: str,
    *,
    text: str,
    sender: str = ROUTE_NAME,
    channel_type: str | None = None,
    platform_id: str | None = None,
) -> str:
    """Write one task/steering message for the agent. Returns its id.

    `channel_type`/`platform_id` are NOT optional decoration -- they are the
    host's half of nanoclaw's routing contract, and omitting them silently
    breaks delivery (verified live). The runner renders each inbound message's
    ``from="..."`` attribute via `findByRouting(channel_type, platform_id)`
    against the `destinations` table; with both NULL the lookup misses, no
    `from` attribute is emitted, and the model -- told to "reply to the
    destination it came from" -- has to guess a name off the visible sender.
    It guessed `oc8`, the destination is `operator`, and the harness dropped
    the answer as an unknown destination while still acking the batch
    `completed`: a run that "succeeds" with no output. The same columns are
    also copied onto every outbound row by `extractRouting`, so leaving them
    NULL strips routing from the replies too.

    `sender` defaults to the same name for the same reason: it is what the
    model sees as the sender, so it must be the destination's name and not a
    second word for the same party.

    The seq is computed BEFORE the write connection opens, so this never holds
    a write connection open while `next_even_seq` opens (and must close) two
    more of its own -- nesting them would still work, but it multiplies the
    number of live connections/fds for no reason.
    """
    message_id = str(uuid.uuid4())
    seq = next_even_seq(session)
    content = json.dumps(
        {"sender": sender, "senderId": sender, "text": text, "isFromMe": False},
        ensure_ascii=False,
    )
    with closing(_connect(os.path.join(session, "inbound.db"))) as conn, conn:
        conn.execute(
            "INSERT INTO messages_in"
            " (id, seq, kind, timestamp, status, trigger, channel_type, platform_id, content) "
            "VALUES (?, ?, 'chat', ?, 'pending', 1, ?, ?, ?)",
            (message_id, seq, _now(), channel_type, platform_id, content),
        )
    return message_id


def read_messages_out(session: str, *, after_seq: int) -> list[OutRow]:
    path = os.path.join(session, "outbound.db")
    if not os.path.exists(path):
        return []
    with closing(_connect(path, readonly=True)) as conn:
        rows = conn.execute(
            "SELECT seq, kind, content, timestamp FROM messages_out WHERE seq > ? ORDER BY seq",
            (after_seq,),
        ).fetchall()
    return [OutRow(int(r[0]), str(r[1]), str(r[2]), str(r[3])) for r in rows]


def ack_status(session: str, message_id: str) -> str | None:
    """processing|completed|failed, or None while the container has not touched it."""
    path = os.path.join(session, "outbound.db")
    if not os.path.exists(path):
        return None
    with closing(_connect(path, readonly=True)) as conn:
        row = conn.execute(
            "SELECT status FROM processing_ack WHERE message_id = ?", (message_id,)
        ).fetchone()
    return str(row[0]) if row else None


def heartbeat_age_s(session: str, *, since: float | None = None) -> float | None:
    """Seconds since the container last touched .heartbeat, or None if never.

    `since` is this run's wall-clock start. A heartbeat older than that was NOT
    written by this run's container, so it says nothing about whether that
    container is alive -- and treating it as a reading is actively harmful: it
    is instantly "stale", so the caller's stale-heartbeat watchdog kills a
    perfectly healthy run seconds after it starts. Observed live: a session
    folder created at 03:40 already held a `.heartbeat` stamped 03:24, and the
    run was failed as "the agent container stopped responding" 5s in, with a
    reported age of 942s. Returning None instead puts that case back under the
    caller's startup grace, which is exactly the "no heartbeat yet" state it
    really is.

    Wall clock, not monotonic, because it is compared against an mtime -- and
    the two agree here: the container writes the file through the same bind
    mount the host reads, with no measurable clock skew (verified: 0.1s).
    """
    path = os.path.join(session, ".heartbeat")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    if since is not None and mtime < since:
        return None
    return max(0.0, time.time() - mtime)
