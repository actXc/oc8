# backend/tests/authz/test_pdp.py
from __future__ import annotations

from oc8.authz.pdp import (
    Effect,
    ToolPolicy,
    authorize_tool,
    classification_rule,
    effective_cleared_classes,
    effective_tool_policies,
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
    frame = _frame(write=True, approval_actions=["write"])
    decision = authorize_tool(frame, {}, tool_key="crm", action="write")
    assert decision.effect is Effect.REQUIRE_APPROVAL
    assert "write" in decision.reason
    assert "always needs approval" in decision.reason


def test_approval_action_forces_approval_even_without_a_euro_threshold() -> None:
    frame = _frame(send=True, approval_actions=["send"])
    # No approval_eur is set at all -- the old mechanism could not express this.
    decision = authorize_tool(frame, {}, tool_key="crm", action="send", value_eur=0)
    assert decision.effect is Effect.REQUIRE_APPROVAL


def test_approval_action_wins_over_the_euro_threshold() -> None:
    # If it were compared, a low value would ALLOW. It must not be compared.
    frame = _frame(send=True, approval_actions=["send"], approval_eur=100000)
    decision = authorize_tool(frame, {}, tool_key="crm", action="send", value_eur=1)
    assert decision.effect is Effect.REQUIRE_APPROVAL
    assert "always needs approval" in decision.reason


def test_narrowing_cannot_remove_a_frame_approval_requirement() -> None:
    frame = _frame(send=True, approval_actions=["send"])
    narrowing = {
        "tools": {"crm": {"enabled": True, "read": True, "send": True, "approval_actions": []}}
    }
    eff = effective_tool_policies(frame, narrowing)
    assert "send" in eff["crm"].approval_actions
    decision = authorize_tool(frame, narrowing, tool_key="crm", action="send", value_eur=0)
    assert decision.effect is Effect.REQUIRE_APPROVAL


def test_narrowing_can_add_an_approval_requirement_the_frame_lacked() -> None:
    frame = _frame(write=True)
    narrowing = {
        "tools": {
            "crm": {"enabled": True, "read": True, "write": True, "approval_actions": ["write"]}
        }
    }
    eff = effective_tool_policies(frame, narrowing)
    assert "write" in eff["crm"].approval_actions


def test_absent_approval_actions_behaves_exactly_as_before() -> None:
    frame = _frame(send=True, approval_eur=500)
    decision_below = authorize_tool(frame, {}, tool_key="crm", action="send", value_eur=100)
    decision_above = authorize_tool(frame, {}, tool_key="crm", action="send", value_eur=500)
    assert decision_below.effect is Effect.ALLOW
    assert decision_above.effect is Effect.REQUIRE_APPROVAL


def test_to_json_round_trips_approval_actions() -> None:
    policy = ToolPolicy(enabled=True, read=True, write=True, approval_actions=frozenset({"write"}))
    restored = ToolPolicy.from_json(policy.to_json())
    assert restored.approval_actions == frozenset({"write"})


def test_approval_action_by_tool_name_forces_approval_even_though_the_right_is_not_listed() -> None:
    # "post_message" is a TOOL, not a right. The right it exercises ("send")
    # is deliberately absent from approval_actions -- only the tool name is
    # listed, and that alone must be enough to require a human.
    frame = {
        "tools": {
            "post_message": {"enabled": True, "send": True, "approval_actions": ["post_message"]}
        }
    }
    decision = authorize_tool(frame, {}, tool_key="post_message", action="send")
    assert decision.effect is Effect.REQUIRE_APPROVAL
    assert "post_message" in decision.reason


def test_approval_action_by_tool_name_does_not_gate_other_tools_sharing_the_right() -> None:
    # Precision, not just strictness: naming "post_message" must leave
    # "create_record" -- which shares the "send" right -- untouched.
    frame = {
        "tools": {
            "post_message": {"enabled": True, "send": True, "approval_actions": ["post_message"]},
            "create_record": {"enabled": True, "send": True},
        }
    }
    decision = authorize_tool(frame, {}, tool_key="create_record", action="send")
    assert decision.effect is Effect.ALLOW


def test_narrowing_cannot_remove_a_frame_tool_name_approval_requirement() -> None:
    # Same frame-union-narrowing guarantee as the right-based case above, but
    # for a tool-name entry: a narrowing must not be able to drop it.
    frame = {
        "tools": {
            "post_message": {"enabled": True, "send": True, "approval_actions": ["post_message"]}
        }
    }
    narrowing = {"tools": {"post_message": {"enabled": True, "send": True, "approval_actions": []}}}
    eff = effective_tool_policies(frame, narrowing)
    assert "post_message" in eff["post_message"].approval_actions
    decision = authorize_tool(frame, narrowing, tool_key="post_message", action="send")
    assert decision.effect is Effect.REQUIRE_APPROVAL


def test_narrowing_can_add_a_tool_name_approval_requirement_the_frame_lacked() -> None:
    # Mirror of test_narrowing_can_add_an_approval_requirement_the_frame_lacked,
    # but the added entry is a tool name rather than a right.
    frame = _frame(send=True)
    narrowing = {
        "tools": {"crm": {"enabled": True, "read": True, "send": True, "approval_actions": ["crm"]}}
    }
    eff = effective_tool_policies(frame, narrowing)
    assert "crm" in eff["crm"].approval_actions
    decision = authorize_tool(frame, narrowing, tool_key="crm", action="send")
    assert decision.effect is Effect.REQUIRE_APPROVAL
