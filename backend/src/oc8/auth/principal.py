"""The authenticated caller."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel

PrincipalKind = Literal["operator", "agent", "plugin"]


class Principal(BaseModel):
    """An authenticated operator, agent, or plugin, scoped to one tenant."""

    subject: str
    tenant_id: uuid.UUID
    role: str
    kind: PrincipalKind = "operator"
    scopes: list[str] = []

    @property
    def is_agent(self) -> bool:
        return self.kind == "agent"
