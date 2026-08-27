"""Governed components: a small, core-owned catalogue of generic layouts an
agent may render in its run transcript instead of only prose (see
docs/superpowers/plans/2026-08-21-governed-components.md).

Core ships the LAYOUT only -- a title, an optional subtitle, a handful of
label/value pairs, an optional link. What goes INSIDE one is whatever the
agent already has from an ordinary, already-authorized MCP tool call earlier
in the same run: this module never fetches anything and names no vendor,
matching the rule the rest of oc8.agent.control_tools already holds. Whether
an agent may use a given layout AT ALL is a separate question, answered by
oc8.models.components.ComponentGrant, not by this catalogue.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RecordCardField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(..., max_length=200)
    value: str = Field(..., max_length=500)


class RecordCardProps(BaseModel):
    """Props for the "record_card" layout: one entity, its key facts, an
    optional link back to the source system it came from."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(..., max_length=200)
    subtitle: str | None = Field(default=None, max_length=200)
    fields: list[RecordCardField] = Field(default_factory=list, max_length=20)
    link_label: str | None = Field(default=None, max_length=200)
    link_url: str | None = Field(default=None, max_length=2048)


#: The whole catalogue. A component_key not in here does not exist -- the
#: first of the three governance checks (exists / granted / valid props).
#: Every entry here is implicitly available to a granted agent; nothing is
#: staged here that isn't meant to be used yet.
COMPONENT_CATALOG: dict[str, type[BaseModel]] = {
    "record_card": RecordCardProps,
}
