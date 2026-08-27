"""Build Claude-compatible hook event payloads from oc8 runtime context."""

from __future__ import annotations

import uuid
from typing import Any


def base_payload(
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"tenant_id": str(tenant_id)}
    if agent_id is not None:
        out["agent_id"] = str(agent_id)
    if run_id is not None:
        out["run_id"] = str(run_id)
    if task_id is not None:
        out["task_id"] = str(task_id)
    return out


def session_start(*, prompt: str = "", **kwargs: Any) -> dict[str, Any]:
    payload = base_payload(**kwargs)
    payload["prompt"] = prompt
    return payload


def user_prompt_submit(*, prompt: str, **kwargs: Any) -> dict[str, Any]:
    payload = base_payload(**kwargs)
    payload["prompt"] = prompt
    return payload


def tool_event(*, tool_name: str, tool_input: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    payload = base_payload(**kwargs)
    payload["tool_name"] = tool_name
    payload["tool_input"] = tool_input
    return payload


def tool_result(*, tool_name: str, tool_input: dict[str, Any], result: str, **kwargs: Any) -> dict[str, Any]:
    payload = tool_event(tool_name=tool_name, tool_input=tool_input, **kwargs)
    payload["tool_response"] = result
    return payload


def task_created(*, title: str, **kwargs: Any) -> dict[str, Any]:
    payload = base_payload(**kwargs)
    payload["title"] = title
    return payload
