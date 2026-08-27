"""Per-connection tool-description augmentation -- a neutral seam, the same
shape as focus_spec/value_spec/outward_tools (oc8.agent.focus,
oc8.agent.control_tools): core reads a plain dict off the connection's own
config and applies it generically, with zero awareness of what any specific
tool actually does. A plugin's tool_pack.toml is the only place software-
specific guidance (e.g. "this Odoo model has no sla_date field") may live --
see docs/superpowers project note "Core must be software-neutral".

Exists because an MCP server's own tool descriptions (mcp_client.py's
McpSession.tools, built straight from list_tools()) can't name a live
instance's own data shape -- Odoo's model/field set is configured per
install, not fixed by the tool schema. Without a way to say "this field
doesn't exist here", a model guesses, live-observed 2026-08-26: opaas_ai's
deterministic sampling made it guess the SAME wrong field on every fresh
run of the same trigger, not just occasionally.
"""

from __future__ import annotations

from typing import Any

from oc8.modelrouter.types import NeutralTool


def apply_tool_notes(tools: list[NeutralTool], cfg: dict[str, Any]) -> list[NeutralTool]:
    notes = cfg.get("tool_notes")
    if not isinstance(notes, dict) or not notes:
        return tools
    return [
        NeutralTool(
            name=t.name,
            description=f"{t.description}\n\n{notes[t.name]}" if t.name in notes else t.description,
            parameters=t.parameters,
        )
        for t in tools
    ]
