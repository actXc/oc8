"""Human-attributable names for the containers oc8 starts.

Without a name Docker invents one (`tender_lamarr`), and on a host running
several agents at once the only way to tell whose container is whose is to
inspect each one's mounts. The shape is `oc8-<who>-<job>-<run>-<unique>`, so
`docker ps` answers "which agent, doing what, for which run" directly, and the
run fragment can be pasted back into an API query.

Software-neutral by design: `who` and `job` are plain strings the caller
chooses, so nothing about any particular runtime leaks into core.
"""

from __future__ import annotations

import re
import unicodedata
import uuid

#: Docker allows `[a-zA-Z0-9][a-zA-Z0-9_.-]*`. We stay inside a stricter subset.
_ILLEGAL = re.compile(r"[^a-z0-9]+")
_PREFIX = "oc8"
_MAX_PART = 20
#: Enough to be unique in practice; short enough to keep the name readable.
_UNIQUE_CHARS = 6


def slug(raw: str, *, fallback: str = "x") -> str:
    """A Docker-legal fragment of free text.

    Agent names are typed by tenants -- umlauts, spaces, emoji, slashes all
    occur -- and handing one straight to Docker fails the run at provision time,
    which is a confusing way to learn that someone named an agent "Süd/West".
    """
    # NFKD first so "ü" degrades to "u" rather than vanishing entirely, which
    # would turn "Süd" into "sd".
    decomposed = unicodedata.normalize("NFKD", raw)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    cleaned = _ILLEGAL.sub("-", ascii_only.lower()).strip("-")
    return cleaned[:_MAX_PART].strip("-") or fallback


def container_name(who: str, job: str, run_id: uuid.UUID | None = None) -> str:
    """`oc8-nora-agent-019fa438-1a2b3c` for agent Nora's run 019fa438…

    The unique tail is not decoration: a run that parks and resumes starts a
    SECOND container, and a leftover from a crashed run may still hold the name.
    Docker refuses a duplicate outright, so a name derived only from the run
    would break exactly the resume path it is meant to describe.
    """
    parts = [_PREFIX, slug(who, fallback="agent"), slug(job, fallback="job")]
    if run_id is not None:
        parts.append(str(run_id)[:8])
    parts.append(uuid.uuid4().hex[:_UNIQUE_CHARS])
    return "-".join(parts)
