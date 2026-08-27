"""Core hook-point declarations (§13.4). Security-relevant points are
non-replaceable (enforced by the registry)."""

from __future__ import annotations

from oc8.hooks.registry import HookRegistry


def register_core_points(reg: HookRegistry) -> None:
    """Declare the core extension points into ``reg``. Idempotent:
    ``declare_point`` overwrites the same name, so calling this more than
    once for the same registry (e.g. once per tenant registry creation,
    once per test) is harmless. There is no longer a default/no-arg form --
    registries are per-tenant, so there's no single global registry to
    declare into implicitly (see ``oc8.hooks.registry.get_hook_registry``)."""
    r = reg
    # Extension points plugins may hook:
    r.declare_point("task.before_create", "filter", replaceable=True)
    r.declare_point("handoff.status.changed", "action")
    r.declare_point("supervision.checkpoint.recorded", "action")
    # Security-relevant, non-replaceable (declared to prove the guardrail):
    r.declare_point("pdp.tool.authorize", "action")
    r.declare_point("audit.event.write", "action")
    r.declare_point("metering.usage.record", "action")
