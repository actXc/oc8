from __future__ import annotations

import pytest
from cli_harness.tail import tail_new_lines

from oc8.sandbox.types import SandboxHandle

pytestmark = pytest.mark.asyncio

_HANDLE = SandboxHandle(container_id="c1", image="i1")


class _TruncatingFakeDriver:
    """Mirrors docker_driver.py's real logs() contract: a FIXED-SIZE trailing
    window, not the full ever-growing log. The bug this fake exists to catch:
    a tail helper using byte-offset bookkeeping instead of overlap-matching
    silently drops whatever falls off the front of the window once
    cumulative output exceeds the window size."""

    def __init__(self, full_log: str, *, window: int = 20) -> None:
        self.full_log = full_log
        self.window = window
        self.emitted = 0  # how many chars of full_log have been "written" so far

    def emit(self, n: int) -> None:
        self.emitted = min(self.emitted + n, len(self.full_log))

    async def logs(self, handle: SandboxHandle) -> str:
        visible = self.full_log[: self.emitted]
        return visible[-self.window :]


async def test_first_call_against_content_already_present_returns_it_as_new() -> None:
    driver = _TruncatingFakeDriver('{"a":1}\n{"b":2}\n', window=200)
    driver.emit(len(driver.full_log))
    lines, raw = await tail_new_lines(driver, _HANDLE, previous_raw="")
    assert lines == ['{"a":1}', '{"b":2}']
    assert raw == '{"a":1}\n{"b":2}\n'


async def test_second_call_within_window_returns_only_new_lines() -> None:
    driver = _TruncatingFakeDriver('{"a":1}\n{"b":2}\n', window=200)
    driver.emit(len('{"a":1}\n'))
    lines, raw = await tail_new_lines(driver, _HANDLE, previous_raw="")
    assert lines == ['{"a":1}']

    driver.emit(len('{"b":2}\n'))
    lines2, raw2 = await tail_new_lines(driver, _HANDLE, previous_raw=raw)
    assert lines2 == ['{"b":2}']
    assert raw2 != raw


async def test_no_new_output_returns_an_empty_list() -> None:
    driver = _TruncatingFakeDriver('{"a":1}\n', window=200)
    driver.emit(len(driver.full_log))
    _, raw = await tail_new_lines(driver, _HANDLE, previous_raw="")
    lines2, raw2 = await tail_new_lines(driver, _HANDLE, previous_raw=raw)
    assert lines2 == []
    assert raw2 == raw


async def test_blank_lines_are_dropped() -> None:
    driver = _TruncatingFakeDriver('{"a":1}\n\n\n{"b":2}\n', window=200)
    driver.emit(len(driver.full_log))
    lines, _ = await tail_new_lines(driver, _HANDLE, previous_raw="")
    assert lines == ['{"a":1}', '{"b":2}']


async def test_sliding_window_still_yields_every_line_with_no_gaps() -> None:
    # Each event line is well under the 20-char window individually, but
    # cumulative output over several polls exceeds it -- exactly the
    # real docker_driver.py situation (4000-char window, multi-turn session).
    lines_in = [f'{{"i":{i}}}\n' for i in range(10)]
    full_log = "".join(lines_in)
    driver = _TruncatingFakeDriver(full_log, window=20)

    collected: list[str] = []
    raw = ""
    for line in lines_in:
        driver.emit(len(line))
        new_lines, raw = await tail_new_lines(driver, _HANDLE, previous_raw=raw)
        collected.extend(new_lines)

    assert collected == [f'{{"i":{i}}}' for i in range(10)]
