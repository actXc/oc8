"""A trusted in-process hook handler contributed by a plugin."""

from __future__ import annotations

from typing import Any


def tag_task(ctx: Any, data: Any) -> Any:
    data = dict(data)
    data["title"] = f"[demo] {data.get('title', '')}"
    return data


def undeclared(ctx: Any, data: Any) -> Any:  # pragma: no cover - must never run
    raise AssertionError("a point not declared in `handles` must not be registered")


def register(contrib: Any) -> None:
    contrib.add_hook("task.before_create", tag_task)
    # Deliberately also offers a point the manifest never declared.
    contrib.add_hook("run.before_start", undeclared)
