"""A minimal RuntimeAdapter contributed by a plugin."""

from __future__ import annotations

from typing import Any

from oc8.runtime.adapter import EchoRuntimeStub


class DemoRuntime(EchoRuntimeStub):
    """Behaves like the echo stub; only its identity matters to the tests."""


def register(contrib: Any) -> None:
    contrib.add_runtime(DemoRuntime)
