"""Incremental log tailing over SandboxDriver.logs().

The real implementation (docker_driver.py's DockerSandboxDriver.logs()) does
NOT return the full ever-growing log each call -- it returns a fixed 4000-char
TRAILING WINDOW (`container.logs().decode(...)[-4000:]`). Once a container's
cumulative stdout exceeds that window, the window SLIDES rather than grows:
content that was visible on one poll can fall off the front by the next.
Byte-offset bookkeeping against such a window silently drops whatever fell
off the front -- including, potentially, a complete JSON event line such as
the waiting_for_approval/waiting_for_input transition a poll loop depends on
this module to surface. So this module tracks the previous RAW STRING and
diffs by finding the longest overlap between the old tail and the new
window, which is correct whether the underlying logs() call happens to grow
without bound or truncate to a sliding window. Shared so three plugins don't
each reimplement this slightly differently (and don't each inherit the same
bug if they got it wrong)."""

from __future__ import annotations

from oc8.sandbox.driver import SandboxDriver
from oc8.sandbox.types import SandboxHandle


def _overlap_length(old: str, new: str) -> int:
    """The longest suffix of `old` that is also a prefix of `new` -- i.e. how
    much of `new`'s start is content we already saw in `old`. Needed because
    the real SandboxDriver (docker_driver.py) truncates logs() to a fixed
    trailing window rather than returning the full ever-growing log, so
    between two polls the window can SLIDE (old content drops off the front)
    rather than simply grow. Byte-offset bookkeeping breaks under a sliding
    window; overlap-matching does not, and degrades safely (0 overlap, i.e.
    'everything in `new` is new') if the window moved past `old` entirely
    rather than silently returning stale/duplicate content as new."""
    max_check = min(len(old), len(new))
    for k in range(max_check, 0, -1):
        if old[-k:] == new[:k]:
            return k
    return 0


async def tail_new_lines(
    driver: SandboxDriver, handle: SandboxHandle, *, previous_raw: str
) -> tuple[list[str], str]:
    raw = await driver.logs(handle)
    overlap = _overlap_length(previous_raw, raw)
    new_text = raw[overlap:]
    lines = [line for line in new_text.splitlines() if line.strip()]
    return lines, raw
