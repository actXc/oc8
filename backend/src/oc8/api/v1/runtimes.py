"""`GET /runtimes`: the read side of agent-runtime selection (§8.7).

Answers "which runtimes may this tenant assign to an agent" -- the list a
hire-time or reassignment picker renders. The list always opens with the two
built-in entries (BUILTIN_IN_PROCESS_RUNTIME_REF / BUILTIN_ISOLATED_RUNTIME_REF,
oc8.runtime.registry), because an agent with no `runtime_ref` set still runs
SOMEWHERE, and at least one of them must never be missing or unavailable: a
picker that can come back empty is a picker that can block a hire. Both are
independently selectable -- an agent that explicitly picks one keeps it
regardless of the tenant's `agent_isolation` setting; an agent that never
picks either still resolves via that setting exactly as before (see
`resolve_runtime`). `is_default=True` marks whichever of the two currently
matches that implicit fallback, so the picker can show "(current default)"
without a second DTO field. Every other entry is a `runtime_adapter` plugin
this tenant has installed AND enabled -- the exact predicate
`oc8.runtime.registry.load_runtime_plugin` already applies to a single
plugin, reused here across all of them so the two paths cannot drift on what
"enabled" means.

A plugin whose implementation is not loadable (bad entry point, plugin
folder missing, import error) stays in the list with `available=False` and
an `unavailableReason` rather than being silently dropped -- an
administrator who installed a runtime and cannot find it has no way to
diagnose a disappearance, but a visible, disabled row with a reason answers
the question on the screen where it was asked.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from oc8.api.deps import DbSession, require_departmental
from oc8.authz.permissions import AGENT, VIEW, perm
from oc8.authz.scope import HumanActor
from oc8.config import get_settings
from oc8.runtime.registry import (
    BUILTIN_IN_PROCESS_CAPABILITIES,
    BUILTIN_IN_PROCESS_RUNTIME_REF,
    BUILTIN_ISOLATED_CAPABILITIES,
    BUILTIN_ISOLATED_RUNTIME_REF,
    is_runtime_executable,
    list_enabled_runtime_plugins_for_tenant,
)
from oc8.schemas.dto import RuntimeOptionDTO

router = APIRouter()


def _builtin_entries() -> list[RuntimeOptionDTO]:
    # `is_default` mirrors resolve_runtime's own agent_isolation fallback
    # exactly (registry.py): whichever entry an agent with runtime_ref unset
    # actually gets is the one this marks -- an entry claiming that status (or
    # a capability list) other than what an agent with that assignment
    # actually gets would be a picker that lies.
    isolated_is_implicit_default = get_settings().agent_isolation
    return [
        RuntimeOptionDTO(
            id=BUILTIN_IN_PROCESS_RUNTIME_REF,
            name="oc8.agent-runtime",
            label="Standard (in-process)",
            summary=(
                "Runs the agent in the shared OC8 process alongside the other "
                "agents on this tenant."
            ),
            capabilities=BUILTIN_IN_PROCESS_CAPABILITIES,
            is_default=not isolated_is_implicit_default,
            available=True,
            unavailable_reason=None,
        ),
        RuntimeOptionDTO(
            id=BUILTIN_ISOLATED_RUNTIME_REF,
            name="oc8.agent-runtime-isolated",
            label="Isolated (per-agent container)",
            summary=(
                "Runs the agent in its own Docker container, isolated from the "
                "other agents on this tenant."
            ),
            capabilities=BUILTIN_ISOLATED_CAPABILITIES,
            is_default=isolated_is_implicit_default,
            available=True,
            unavailable_reason=None,
        ),
    ]


@router.get(
    "/runtimes",
    response_model=list[RuntimeOptionDTO],
)
async def list_runtimes(
    db: DbSession,
    actor: Annotated[HumanActor, Depends(require_departmental(perm(AGENT, VIEW)))],
) -> list[RuntimeOptionDTO]:
    entries = _builtin_entries()
    for plugin, version in await list_enabled_runtime_plugins_for_tenant(
        db, tenant_id=actor.principal.tenant_id
    ):
        available = is_runtime_executable(plugin.name)
        entries.append(
            RuntimeOptionDTO(
                id=str(plugin.id),
                name=plugin.name,
                label=str(version.manifest.get("label") or plugin.name),
                summary=str(version.manifest.get("summary") or ""),
                capabilities=list(version.capabilities),
                is_default=False,
                available=available,
                unavailable_reason=None if available else "plugin code is not loadable",
            )
        )
    return entries
