# backend/tests/authz/test_pdp.py
from __future__ import annotations

from oc8.authz.pdp import (
    Effect,
    ToolPolicy,
    authorize_tool,
    classification_rule,
    effective_cleared_classes,
    effective_tool_policies,
    narrowing_within_frame,
)


def test_effective_cleared_classes_default_excludes_restricted() -> None:
    cleared = effective_cleared_classes({})
    assert cleared == {"public", "internal", "confidential"}
    assert "restricted" not in cleared


def test_effective_cleared_classes_default_when_key_missing() -> None:
    cleared = effective_cleared_classes({"tools": {}})
    assert cleared == {"public", "internal", "confidential"}


def test_effective_cleared_classes_reads_explicit_frame_value() -> None:
    cleared = effective_cleared_classes({"cleared_classes": ["public", "restricted"]})
    assert cleared == {"public", "restricted"}


def test_classification_rule_denies_uncleared_classification() -> None:
    decision = classification_rule("confidential", frozenset({"public"}), "cloud")
    assert decision.effect is Effect.DENY


def test_classification_rule_denies_restricted_on_cloud_model() -> None:
    decision = classification_rule("restricted", frozenset({"restricted"}), "cloud")
    assert decision.effect is Effect.DENY


def test_classification_rule_allows_restricted_on_local_model() -> None:
    decision = classification_rule("restricted", frozenset({"restricted"}), "local")
    assert decision.effect is Effect.ALLOW


def test_classification_rule_allows_non_restricted_regardless_of_locality() -> None:
    decision = classification_rule("confidential", frozenset({"confidential"}), "cloud")
    assert decision.effect is Effect.ALLOW


def test_classification_rule_denies_restricted_even_when_cleared_but_cloud() -> None:
    # cleared_classes includes 'restricted' -- clearance alone isn't enough,
    # the locality gate still applies on top.
    decision = classification_rule("restricted", frozenset({"public", "restricted"}), "cloud")
    assert decision.effect is Effect.DENY


def _frame(**tool_kwargs: object) -> dict:
    return {"tools": {"crm": {"enabled": True, "read": True, **tool_kwargs}}}


def test_approval_action_forces_approval_regardless_of_value() -> None:
    frame = _frame(modify=True, approval_actions=["modify"])
    decision = authorize_tool(frame, {}, tool_key="crm", action="modify")
    assert decision.effect is Effect.REQUIRE_APPROVAL
    assert "modify" in decision.reason
    assert "always needs approval" in decision.reason


def test_approval_action_forces_approval_even_without_a_euro_threshold() -> None:
    frame = _frame(modify=True, approval_actions=["modify"])
    # No approval_eur is set at all -- the old mechanism could not express this.
    decision = authorize_tool(frame, {}, tool_key="crm", action="modify", value_eur=0)
    assert decision.effect is Effect.REQUIRE_APPROVAL


def test_approval_action_wins_over_the_euro_threshold() -> None:
    # If it were compared, a low value would ALLOW. It must not be compared.
    frame = _frame(modify=True, approval_actions=["modify"], approval_eur=100000)
    decision = authorize_tool(frame, {}, tool_key="crm", action="modify", value_eur=1)
    assert decision.effect is Effect.REQUIRE_APPROVAL
    assert "always needs approval" in decision.reason


def test_narrowing_cannot_remove_a_frame_approval_requirement() -> None:
    frame = _frame(modify=True, approval_actions=["modify"])
    narrowing = {
        "tools": {"crm": {"enabled": True, "read": True, "modify": True, "approval_actions": []}}
    }
    eff = effective_tool_policies(frame, narrowing)
    assert "modify" in eff["crm"].approval_actions
    decision = authorize_tool(frame, narrowing, tool_key="crm", action="modify", value_eur=0)
    assert decision.effect is Effect.REQUIRE_APPROVAL


def test_narrowing_can_add_an_approval_requirement_the_frame_lacked() -> None:
    frame = _frame(modify=True)
    narrowing = {
        "tools": {
            "crm": {"enabled": True, "read": True, "modify": True, "approval_actions": ["modify"]}
        }
    }
    eff = effective_tool_policies(frame, narrowing)
    assert "modify" in eff["crm"].approval_actions


def test_absent_approval_actions_behaves_exactly_as_before() -> None:
    frame = _frame(modify=True, approval_eur=500)
    decision_below = authorize_tool(frame, {}, tool_key="crm", action="modify", value_eur=100)
    decision_above = authorize_tool(frame, {}, tool_key="crm", action="modify", value_eur=500)
    assert decision_below.effect is Effect.ALLOW
    assert decision_above.effect is Effect.REQUIRE_APPROVAL


def test_to_json_round_trips_approval_actions() -> None:
    policy = ToolPolicy(enabled=True, read=True, modify=True, approval_actions=frozenset({"modify"}))
    restored = ToolPolicy.from_json(policy.to_json())
    assert restored.approval_actions == frozenset({"modify"})


def test_approval_action_by_tool_name_forces_approval_even_though_the_right_is_not_listed() -> None:
    # "post_message" is a TOOL, not a right. The right it exercises ("modify")
    # is deliberately absent from approval_actions -- only the tool name is
    # listed, and that alone must be enough to require a human.
    frame = {
        "tools": {
            "post_message": {"enabled": True, "modify": True, "approval_actions": ["post_message"]}
        }
    }
    decision = authorize_tool(frame, {}, tool_key="post_message", action="modify")
    assert decision.effect is Effect.REQUIRE_APPROVAL
    assert "post_message" in decision.reason


def test_approval_action_by_tool_name_does_not_gate_other_tools_sharing_the_right() -> None:
    # Precision, not just strictness: naming "post_message" must leave
    # "create_record" -- which shares the "modify" right -- untouched.
    frame = {
        "tools": {
            "post_message": {"enabled": True, "modify": True, "approval_actions": ["post_message"]},
            "create_record": {"enabled": True, "modify": True},
        }
    }
    decision = authorize_tool(frame, {}, tool_key="create_record", action="modify")
    assert decision.effect is Effect.ALLOW


def test_narrowing_cannot_remove_a_frame_tool_name_approval_requirement() -> None:
    # Same frame-union-narrowing guarantee as the right-based case above, but
    # for a tool-name entry: a narrowing must not be able to drop it.
    frame = {
        "tools": {
            "post_message": {"enabled": True, "modify": True, "approval_actions": ["post_message"]}
        }
    }
    narrowing = {"tools": {"post_message": {"enabled": True, "modify": True, "approval_actions": []}}}
    eff = effective_tool_policies(frame, narrowing)
    assert "post_message" in eff["post_message"].approval_actions
    decision = authorize_tool(frame, narrowing, tool_key="post_message", action="modify")
    assert decision.effect is Effect.REQUIRE_APPROVAL


def test_narrowing_can_add_a_tool_name_approval_requirement_the_frame_lacked() -> None:
    # Mirror of test_narrowing_can_add_an_approval_requirement_the_frame_lacked,
    # but the added entry is a tool name rather than a right.
    frame = _frame(modify=True)
    narrowing = {
        "tools": {"crm": {"enabled": True, "read": True, "modify": True, "approval_actions": ["crm"]}}
    }
    eff = effective_tool_policies(frame, narrowing)
    assert "crm" in eff["crm"].approval_actions
    decision = authorize_tool(frame, narrowing, tool_key="crm", action="modify")
    assert decision.effect is Effect.REQUIRE_APPROVAL


# ---------- Agent-exclusive grants (tool key absent from the frame) ----------


def test_narrowing_within_frame_does_not_flag_a_tool_absent_from_the_frame() -> None:
    frame = _frame()  # only "crm"
    narrowing = {"tools": {"salesforce": {"enabled": True, "read": True}}}
    assert narrowing_within_frame(frame, narrowing) == []


def test_narrowing_within_frame_still_flags_a_frame_tool_widened_beyond_it() -> None:
    frame = _frame(modify=False)
    narrowing = {"tools": {"crm": {"enabled": True, "read": True, "modify": True}}}
    violations = narrowing_within_frame(frame, narrowing)
    assert any(v.tool_key == "crm" for v in violations)


def test_effective_tool_policies_grants_an_agent_exclusive_tool() -> None:
    # "salesforce" is nowhere in the frame -- no sibling agent inheriting
    # this frame would ever see it.
    frame = _frame()
    narrowing = {
        "tools": {
            "salesforce": {
                "enabled": True,
                "read": True,
                "modify": True,
                "connection_id": "conn-1",
            }
        }
    }
    eff = effective_tool_policies(frame, narrowing)
    assert eff["salesforce"].enabled is True
    assert eff["salesforce"].read is True
    assert eff["salesforce"].modify is True
    assert eff["salesforce"].connection_id == "conn-1"
    # The frame's own tool is unaffected by the agent-exclusive addition.
    assert "crm" in eff
    assert eff["crm"].enabled is True


def test_effective_tool_policies_agent_exclusive_tool_disabled_stays_disabled() -> None:
    frame = _frame()
    narrowing = {"tools": {"salesforce": {"enabled": False, "read": True}}}
    eff = effective_tool_policies(frame, narrowing)
    assert eff["salesforce"].enabled is False


# ---------- New tests for modify right (replacing write+send) ----------


def test_modify_right_replaces_write_and_send() -> None:
    frame = {"tools": {"odoo": ToolPolicy(enabled=True, read=True, modify=True).to_json()}}
    policies = effective_tool_policies(frame, {})
    assert policies["odoo"].modify is True
    assert not hasattr(policies["odoo"], "write")
    assert not hasattr(policies["odoo"], "send")


def test_effective_tool_policies_no_longer_takes_role_rights() -> None:
    import inspect

    sig = inspect.signature(effective_tool_policies)
    assert "role_rights" not in sig.parameters


def test_a_narrowing_that_disables_modify_is_enforced() -> None:
    frame = {"tools": {"odoo": {"enabled": True, "read": True, "modify": True}}}
    narrowing = {"tools": {"odoo": {"enabled": True, "read": True, "modify": False}}}
    decision = authorize_tool(
        frame, narrowing, tool_key="odoo", action="modify", value_eur=None
    )
    assert decision.effect is Effect.DENY
