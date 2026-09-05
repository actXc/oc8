"""Policy Decision Point.

Pure functions over the department frame (the ceiling) and the agent narrowing
(per-agent tightening). The algebra is fixed (tech-spec §5.3):

    effective = department_frame ∩ agent_narrowing

For a tool key the frame grants, an agent narrowing may only *remove*
tools/rights or *lower* an approval threshold — never widen. `narrowing_within_frame`
enforces that at write time; `authorize_tool` enforces it again at decision
time (defense in depth).

A tool key the frame does NOT grant is a different case: an agent may still
be given that tool directly (an agent-exclusive grant, invisible to sibling
agents in the same department), since there is no frame policy to widen
beyond -- the narrowing entry simply becomes that tool's whole policy. See
`effective_tool_policies`'s second loop and `narrowing_within_frame`'s
`key not in frame_tools` branch.

Frame / narrowing JSON shape (stored on ``department.frame`` / ``agent.narrowing``):

    {
      "tools": {
        "<tool_key>": {
          "enabled": bool,
          "read": bool, "modify": bool,
          "approval_eur": int | null,  # threshold at or above which modify needs approval
          "approval_actions": ["modify", ...]  # rights and/or tool keys that always need a human
        }
      },
      "kbs": ["<kb_id>", ...],
      "memory": {"department": ["read","write"], "company": ["read"]}
    }
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

RIGHTS = ("read", "modify")


class Effect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class ToolPolicy:
    enabled: bool = False
    read: bool = False
    modify: bool = False
    approval_eur: int | None = None
    #: Rights and/or tool keys that always need a human, whatever the call is
    #: worth. `approval_eur` answers "how expensive before someone checks";
    #: this answers "which actions -- or which single tool -- does nobody do
    #: unattended" -- the only question a connection without monetary values
    #: (a helpdesk, a knowledge source) can be asked. A right in this set
    #: (e.g. "send") gates every tool that grants it; a tool key (e.g.
    #: "post_message") gates only that tool, leaving other tools sharing the
    #: same right alone -- the precision `only` gives to surface, applied to
    #: approval.
    approval_actions: frozenset[str] = frozenset()
    #: When set, the ONLY tool names this connection may offer. None means all.
    #:
    #: A ceiling has always been about rights; this makes it about surface too,
    #: and for a reason we measured. Connecting a second system took the tool
    #: list from 14 to 35, and the model stopped working: it looped on the same
    #: lookup instead of proceeding. Most of those tools were ones the
    #: department never uses -- a bridge exposes everything its software can do,
    #: which is not the same as everything this department should see.
    #:
    #: It is also the tighter grant. "May write to the issue tracker" and "may
    #: merge pull requests and push commits" were the same permission before.
    only: frozenset[str] | None = None
    # The McpConnection (a Credential-backed login) this tool key's agent has
    # been assigned, or None if unassigned -- agent tool login selection
    # design. Frame-level policies never set this (a department default has
    # no login of its own); only agent-level narrowing does.
    connection_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> ToolPolicy:
        if not data:
            return cls()
        raw_only = data.get("only")
        raw_actions = data.get("approval_actions")
        return cls(
            enabled=bool(data.get("enabled", False)),
            read=bool(data.get("read", False)),
            modify=bool(data.get("modify", False)),
            approval_eur=data.get("approval_eur"),
            approval_actions=frozenset(str(a) for a in raw_actions) if raw_actions else frozenset(),
            only=frozenset(str(t) for t in raw_only) if raw_only else None,
            connection_id=data.get("connection_id"),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "read": self.read,
            "modify": self.modify,
            "approval_eur": self.approval_eur,
            "approval_actions": sorted(self.approval_actions),
            "only": sorted(self.only) if self.only is not None else None,
            "connection_id": self.connection_id,
        }

    def has_right(self, action: str) -> bool:
        return bool(getattr(self, action, False))

    def offers(self, tool: str) -> bool:
        """Whether this connection may expose `tool` at all."""
        return self.only is None or tool in self.only


@dataclass(frozen=True)
class Decision:
    effect: Effect
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.effect is Effect.ALLOW


def _tools(frame: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return frame.get("tools", {}) if frame else {}


def effective_tool_policies(
    frame: dict[str, Any],
    narrowing: dict[str, Any],
) -> dict[str, ToolPolicy]:
    """§5.3's intersection, with frame and narrowing terms.

        effective = frame ∩ narrowing

    The frame and the narrowing intersect: each can only tighten, never widen.
    A tool the frame withholds cannot be granted by the narrowing; a right the
    frame denies cannot be granted by the narrowing. The intersection is taken
    rather than the union, for the same reason `_narrower_surface` gives.
    """
    frame_tools = _tools(frame)
    narrow_tools = _tools(narrowing)
    out: dict[str, ToolPolicy] = {}
    for key, raw in frame_tools.items():
        f = ToolPolicy.from_json(raw)
        n = narrow_tools.get(key)
        no = ToolPolicy.from_json(n) if n is not None else None
        out[key] = ToolPolicy(
            enabled=f.enabled and (no.enabled if no else True),
            read=f.read and (no.read if no else True),
            modify=f.modify and (no.modify if no else True),
            approval_eur=_min_threshold(f.approval_eur, no.approval_eur if no else None),
            approval_actions=f.approval_actions | (no.approval_actions if no else frozenset()),
            only=_narrower_surface(f.only, no.only if no else None),
            # Narrowing-only, unlike every field above: a `connection_id` pin
            # is an AGENT-level login selection (Task 4/6, agent tool login
            # selection design) with no frame-level counterpart to intersect
            # against -- a department default has no login of its own. So
            # this passes through from the narrowing term alone; there is no
            # `f.connection_id` to consider.
            connection_id=no.connection_id if no else None,
        )
    # Agent-exclusive grants: a narrowing key with no frame counterpart at
    # all. There is nothing to intersect against, so the narrowing term IS
    # the policy outright. `narrowing_within_frame` is what allows this key to
    # reach here in the first place (its `key not in frame_tools` branch no
    # longer flags it); this loop is where that grant actually takes effect.
    for key, raw in narrow_tools.items():
        if key in frame_tools:
            continue
        exclusive = ToolPolicy.from_json(raw)
        out[key] = ToolPolicy(
            enabled=exclusive.enabled,
            read=exclusive.read,
            modify=exclusive.modify,
            approval_eur=exclusive.approval_eur,
            approval_actions=exclusive.approval_actions,
            only=exclusive.only,
            connection_id=exclusive.connection_id,
        )
    return out


def _narrower_surface(
    frame_only: frozenset[str] | None, narrow_only: frozenset[str] | None
) -> frozenset[str] | None:
    """An agent may only ever tighten the surface, never widen it.

    Naming a tool the frame withholds must not grant it -- same rule as every
    other field here, and the reason the intersection is taken rather than the
    union.
    """
    if frame_only is None:
        return narrow_only
    if narrow_only is None:
        return frame_only
    return frame_only & narrow_only


def _min_threshold(frame_eur: int | None, narrow_eur: int | None) -> int | None:
    # A lower threshold is stricter; None means "no threshold" (least strict).
    if frame_eur is None:
        return narrow_eur
    if narrow_eur is None:
        return frame_eur
    return min(frame_eur, narrow_eur)


def authorize_tool(
    frame: dict[str, Any],
    narrowing: dict[str, Any],
    *,
    tool_key: str,
    action: str,
    value_eur: float | None = None,
) -> Decision:
    """Authorize a single tool action against the effective policy.

    NOT the runtime's gate -- `authorize_tool_call` below is, reached from
    `agent/engine.py` and `api/mcp_gateway.py`. This one exists because it asks
    a genuinely different question and cannot be reduced to a call to the other
    without changing what either answers:

    - It takes `frame`/`narrowing` directly and resolves them itself via
      `effective_tool_policies`, where `authorize_tool_call` takes an
      already-resolved `policies` mapping (its caller does that resolution
      once per run, not once per call).
    - `tool_key` does double duty here: it is both the policy's lookup key
      AND, for `approval_actions`' tool-name case, the tool identity itself.
      `authorize_tool_call` keeps those separate (`connection_key` vs. `tool`)
      because a connection and the specific MCP tool it exposes are not the
      same string -- an "odoo" connection's `only` list names tools like
      `res_partner_read`, not "odoo". Passing `tool_key` as `tool` here would
      wrongly run it through `policy.offers()`, which this function never
      checks at all.
    - It has no `extra_thresholds`: the skill guardrail and per-agent
      threshold that `authorize_tool_call` folds in have no equivalent input
      here.

    Kept for the semantics it pins down (and the test suite that encodes
    them) rather than deleted; if a caller needs the live runtime path, use
    `authorize_tool_call`.
    """
    if action not in RIGHTS:
        return Decision(Effect.DENY, f"unknown action '{action}'")
    eff = effective_tool_policies(frame, narrowing).get(tool_key)
    if eff is None or not eff.enabled:
        return Decision(Effect.DENY, f"tool '{tool_key}' not enabled for this agent")
    if not eff.has_right(action):
        # Deliberately BEFORE the approval check below: an action the policy
        # does not grant at all is denied, not sent to a human. Listing it in
        # `approval_actions` cannot resurrect it -- there is nothing to approve.
        return Decision(Effect.DENY, f"action '{action}' not permitted on '{tool_key}'")
    if action in eff.approval_actions or tool_key in eff.approval_actions:
        # Wins over the euro threshold below: an operator who asked for a
        # human on every action of this kind (or on this specific tool) has
        # already answered the question the threshold would ask, so the
        # value is never compared. `approval_actions` may name a right
        # (gates every tool granting it) or a tool key (gates only that
        # tool, leaving others that share the right alone).
        reason = (
            f"'{action}' always needs approval on '{tool_key}'"
            if action in eff.approval_actions
            else f"'{tool_key}' always needs approval"
        )
        return Decision(Effect.REQUIRE_APPROVAL, reason)
    if action == "modify" and eff.approval_eur is not None:
        # See authorize_tool_call: an unreadable value counts as zero, so a
        # threshold of €0 means "a human decides every modify".
        effective_value = value_eur if value_eur is not None else 0.0
        if effective_value >= eff.approval_eur:
            return Decision(
                Effect.REQUIRE_APPROVAL,
                f"value €{value_eur:g} meets approval threshold €{eff.approval_eur}"
                if value_eur is not None
                else f"every modify needs approval (threshold €{eff.approval_eur})",
            )
    return Decision(Effect.ALLOW)


_DEFAULT_CLEARED_CLASSES = frozenset({"public", "internal", "confidential"})


def effective_cleared_classes(frame: dict[str, Any]) -> frozenset[str]:
    """The classifications this department's agents may retrieve
    (tech-spec §5.3/§11.5), from frame.cleared_classes. Defaults to
    everything except 'restricted' when unset — no breaking change for
    existing data (KB/chunk default classification is 'internal')."""
    if not frame or "cleared_classes" not in frame:
        return _DEFAULT_CLEARED_CLASSES
    return frozenset(frame["cleared_classes"])


def classification_rule(
    resource_classification: str, cleared_classes: frozenset[str], model_locality: str
) -> Decision:
    """§5.3 step 1 / the §11.5 access-scoping rule, as a reusable pure
    function: DENY if the resource's classification isn't cleared for this
    department; DENY if it's 'restricted' and the model isn't local."""
    if resource_classification not in cleared_classes:
        return Decision(Effect.DENY, f"classification '{resource_classification}' not cleared")
    if resource_classification == "restricted" and model_locality != "local":
        return Decision(Effect.DENY, "restricted classification requires a local model")
    return Decision(Effect.ALLOW)


@dataclass
class SubsetViolation:
    tool_key: str
    reason: str


def narrowing_within_frame(
    frame: dict[str, Any], narrowing: dict[str, Any]
) -> list[SubsetViolation]:
    """Return violations where the narrowing widens a FRAME tool beyond what
    the frame grants (empty = ok). A key absent from the frame entirely is
    not a violation -- it's an agent-exclusive grant (see module docstring
    and `effective_tool_policies`'s second loop), which has no frame policy
    to widen beyond."""
    frame_tools = _tools(frame)
    violations: list[SubsetViolation] = []
    for key, raw in _tools(narrowing).items():
        if key not in frame_tools:
            continue
        n = ToolPolicy.from_json(raw)
        f = ToolPolicy.from_json(frame_tools.get(key))
        if n.enabled and not f.enabled:
            violations.append(SubsetViolation(key, "tool disabled in department frame"))
        for right in RIGHTS:
            if getattr(n, right) and not getattr(f, right):
                violations.append(SubsetViolation(key, f"'{right}' not granted by frame"))
        if (
            f.approval_eur is not None
            and n.approval_eur is not None
            and n.approval_eur > f.approval_eur
        ):
            violations.append(SubsetViolation(key, "approval threshold higher (looser) than frame"))
        if f.approval_eur is not None and n.approval_eur is None:
            violations.append(SubsetViolation(key, "narrowing removes a frame approval threshold"))
    return violations


@dataclass
class MissingRequirement:
    kind: str  # "tool" | "kb"
    key: str
    reason: str


def missing_skill_requirements(
    frame: dict[str, Any],
    narrowing: dict[str, Any],
    requires: dict[str, Any],
    granted_kb_ids: set[str] | None = None,
) -> list[MissingRequirement]:
    """A skill can be assigned only if requires ⊆ effective(agent) (tech-spec §6.5)."""
    eff = effective_tool_policies(frame, narrowing)
    granted_kb_ids = granted_kb_ids or set()
    missing: list[MissingRequirement] = []
    for req in requires.get("tools", []):
        key = str(req.get("tool") if isinstance(req, dict) else req)
        rights = req.get("rights", ["read"]) if isinstance(req, dict) else ["read"]
        pol = eff.get(key)
        if pol is None or not pol.enabled:
            missing.append(MissingRequirement("tool", key, f"'{key}' not enabled"))
            continue
        for right in rights:
            if not pol.has_right(right):
                missing.append(MissingRequirement("tool", key, f"'{right}' on '{key}' not granted"))
    for req in requires.get("kbs", []):
        key = str(req.get("kb_ref") if isinstance(req, dict) else req)
        if key not in granted_kb_ids:
            missing.append(MissingRequirement("kb", key, f"KB '{key}' not granted"))
    return missing


def required_right(tool_name: str, scopes: Mapping[str, Any] | None) -> str:
    """Classify a tool call as read / modify.

    `scopes` is the connection's classification, e.g.
    `{"read": ["fs_read"], "modify": ["send_email"]}`. Anything unlisted requires
    `modify` -- fail-closed, so an unknown tool is treated as the more dangerous
    case rather than waved through.
    """
    if not scopes:
        return "modify"
    for right in RIGHTS:
        names = scopes.get(right)
        if isinstance(names, (list, tuple)) and tool_name in names:
            return right
    return "modify"


def authorize_tool_call(
    *,
    policies: Mapping[str, ToolPolicy],
    connection_key: str | None,
    right: str,
    value: float | None,
    extra_thresholds: Sequence[float | None] = (),
    tool: str | None = None,
) -> Decision:
    """Decide a tool call against the department frame (§5.3).

    `extra_thresholds` carries the agent's own `approval_value_eur` and any
    active skill guardrail; the strictest (lowest non-null) of those and the
    policy's own `approval_eur` governs.

    `tool` is checked against the frame's surface (`only`) where one is set.
    Withholding a tool from the advertised list is not enough on its own: a
    model that has seen the name once, in an earlier run or a mission, will
    call it anyway, and a grant that only hides is not a grant that holds.

    THE live gate: this is what `agent/engine.py` and `api/mcp_gateway.py`
    call for every real tool call. `authorize_tool` above answers a related
    but distinct question over a raw frame/narrowing pair with a single
    conflated `tool_key`; see the comment on it for why the two do not
    collapse into one function.
    """
    if connection_key is None:
        return Decision(Effect.DENY, "no tool connection bound to this run")
    if right not in RIGHTS:
        return Decision(Effect.DENY, f"unknown right {right!r}")
    policy = policies.get(connection_key)
    if policy is None:
        return Decision(Effect.DENY, f"no grant for {connection_key!r} in this frame")
    if not policy.enabled:
        return Decision(Effect.DENY, f"{connection_key!r} is disabled in this frame")
    if not getattr(policy, right, False):
        return Decision(Effect.DENY, f"{connection_key!r} does not grant {right!r}")
    if tool is not None and not policy.offers(tool):
        return Decision(
            Effect.DENY, f"{connection_key!r} does not grant the tool {tool!r} in this frame"
        )

    if right in policy.approval_actions or (tool is not None and tool in policy.approval_actions):
        # Deliberately AFTER every DENY check above and BEFORE the euro
        # threshold below -- same ordering and the same reasoning as
        # `authorize_tool`. An action or tool this frame does not grant at
        # all was already denied above; there is nothing to send to a human
        # in something that is not permitted. And once we know it IS
        # permitted, `approval_actions` wins over `approval_eur`/
        # `extra_thresholds` without comparing the value at all: an operator
        # who asked for a human on every action of this kind (or on this one
        # tool) has already answered the question a threshold would ask.
        # `approval_actions` may name a right (gates every tool granting it)
        # or a tool name (gates only that tool, leaving others sharing the
        # right alone).
        reason = (
            f"{right!r} always needs approval on {connection_key!r}"
            if right in policy.approval_actions
            else f"{tool!r} always needs approval"
        )
        return Decision(Effect.REQUIRE_APPROVAL, reason)

    thresholds = [t for t in (policy.approval_eur, *extra_thresholds) if t is not None]
    if thresholds:
        strictest = min(float(t) for t in thresholds)
        # An unreadable value counts as ZERO, not as "exempt". Otherwise every
        # action without a price -- a reply to a customer, a status change --
        # bypasses approval no matter what the frame says, and a threshold of €0
        # ("a human decides every outward action") cannot be expressed at all.
        # A positive threshold is unaffected: 0 is under it, as before.
        if (value if value is not None else 0.0) >= strictest:
            return Decision(
                Effect.REQUIRE_APPROVAL,
                f"value €{value:g} meets threshold €{strictest:g}"
                if value is not None
                else f"every action needs approval (threshold €{strictest:g})",
            )
    return Decision(Effect.ALLOW)
