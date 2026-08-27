"""Declarative flow spec (validated). A flow orchestrates cross-department
handoffs across stages; it executes no work itself (§14a.4)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, ValidationError


class FlowSpecError(ValueError):
    """A flow spec failed validation."""


class StageHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str  # handoff type name
    to_department_id: uuid.UUID
    payload_map: dict[str, str] = {}
    gate: str = "auto"


class Stage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    handoff: StageHandoff
    after: str | None = None  # e.g. "kickoff.completed"
    condition: str | None = None  # e.g. "$.budget_eur > 0"


class FlowTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: str
    from_department_id: uuid.UUID


class FlowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: str
    trigger: FlowTrigger
    stages: list[Stage]


def parse_flow_spec(data: dict[str, object]) -> FlowSpec:
    try:
        return FlowSpec.model_validate(data)
    except ValidationError as exc:
        raise FlowSpecError(str(exc)) from exc
