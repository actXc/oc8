"""`Condition`/`evaluate_conditions` is the generic "with limits" mechanism
(dev session 2026-09-11): a business rule is `attribute operator value -> then`,
where `attribute` is just a name the caller supplies via `attributes` --
nothing here is euro-shaped or Odoo-shaped. These tests deliberately exercise
non-numeric datatypes (string/enum/boolean) alongside a numeric one, to prove
the resolver is genuinely generic rather than a euro threshold with a new name.
"""

from __future__ import annotations

from oc8.authz.pdp import (
    Condition,
    Decision,
    Effect,
    ToolPolicy,
    authorize_tool_call,
    effective_tool_policies,
    evaluate_conditions,
)


def _policies(**kw: ToolPolicy) -> dict[str, ToolPolicy]:
    return dict(kw)


def test_numeric_condition_matches_above_threshold() -> None:
    condition = Condition(attribute="order_value", datatype="number", operator=">", value=5000)
    decision = evaluate_conditions([condition], {"order_value": 12480})
    assert decision is not None
    assert decision.effect is Effect.REQUIRE_APPROVAL
    assert "order_value" in decision.reason


def test_numeric_condition_does_not_match_below_threshold() -> None:
    condition = Condition(attribute="order_value", datatype="number", operator=">", value=5000)
    assert evaluate_conditions([condition], {"order_value": 100}) is None


def test_string_inequality_condition() -> None:
    # "recipient_domain != company_domain" from the spec's own example --
    # proves the resolver isn't secretly numeric-only.
    condition = Condition(
        attribute="recipient_domain",
        datatype="string",
        operator="!=",
        value="acme.example",
        then=Effect.REQUIRE_APPROVAL,
    )
    external = evaluate_conditions([condition], {"recipient_domain": "gmail.com"})
    assert external is not None and external.effect is Effect.REQUIRE_APPROVAL

    internal = evaluate_conditions([condition], {"recipient_domain": "acme.example"})
    assert internal is None


def test_enum_membership_condition() -> None:
    condition = Condition(
        attribute="environment",
        datatype="enum",
        operator="in",
        value=("staging", "production"),
        then=Effect.DENY,
    )
    matched = evaluate_conditions([condition], {"environment": "production"})
    assert matched is not None and matched.effect is Effect.DENY
    assert evaluate_conditions([condition], {"environment": "dev"}) is None


def test_boolean_equality_condition() -> None:
    condition = Condition(attribute="is_test_order", datatype="boolean", operator="==", value=False)
    assert evaluate_conditions([condition], {"is_test_order": False}) is not None
    assert evaluate_conditions([condition], {"is_test_order": True}) is None


def test_missing_attribute_does_not_match() -> None:
    # A condition authored for an attribute this call never extracted must not
    # crash and must not silently fire.
    condition = Condition(attribute="order_value", operator=">", value=5000)
    assert evaluate_conditions([condition], {}) is None


def test_first_matching_condition_wins() -> None:
    conditions = [
        Condition(attribute="order_value", operator=">", value=50_000, then=Effect.DENY),
        Condition(attribute="order_value", operator=">", value=5_000, then=Effect.REQUIRE_APPROVAL),
    ]
    decision = evaluate_conditions(conditions, {"order_value": 60_000})
    assert decision is not None
    assert decision.effect is Effect.DENY


def test_condition_json_round_trip_preserves_tuple_value() -> None:
    condition = Condition(
        attribute="environment", datatype="enum", operator="in", value=("staging", "production")
    )
    restored = Condition.from_json(condition.to_json())
    assert restored == condition
    assert isinstance(restored.value, tuple)


def test_tool_policy_json_round_trip_includes_conditions() -> None:
    policy = ToolPolicy(
        enabled=True,
        modify=True,
        conditions=(Condition(attribute="order_value", operator=">", value=5000),),
    )
    restored = ToolPolicy.from_json(policy.to_json())
    assert restored.conditions == policy.conditions


def test_effective_tool_policies_unions_conditions_from_frame_and_narrowing() -> None:
    frame = {
        "tools": {
            "odoo": {
                "enabled": True,
                "modify": True,
                "conditions": [{"attribute": "order_value", "operator": ">", "value": 5000}],
            }
        }
    }
    narrowing = {
        "tools": {
            "odoo": {
                "enabled": True,
                "modify": True,
                "conditions": [{"attribute": "quantity", "operator": ">", "value": 100}],
            }
        }
    }
    effective = effective_tool_policies(frame, narrowing)
    attrs = {c.attribute for c in effective["odoo"].conditions}
    assert attrs == {"order_value", "quantity"}


def test_agent_exclusive_grant_keeps_its_own_conditions() -> None:
    narrowing = {
        "tools": {
            "github": {
                "enabled": True,
                "modify": True,
                "conditions": [{"attribute": "branch", "operator": "==", "value": "main"}],
            }
        }
    }
    effective = effective_tool_policies({}, narrowing)
    assert effective["github"].conditions[0].attribute == "branch"


def test_authorize_tool_call_evaluates_conditions_generically() -> None:
    policy = ToolPolicy(
        enabled=True,
        modify=True,
        conditions=(Condition(attribute="order_value", operator=">", value=5000),),
    )
    gated = authorize_tool_call(
        policies=_policies(odoo=policy),
        connection_key="odoo",
        right="modify",
        value=None,
        attributes={"order_value": 12480},
    )
    assert gated.effect is Effect.REQUIRE_APPROVAL

    allowed = authorize_tool_call(
        policies=_policies(odoo=policy),
        connection_key="odoo",
        right="modify",
        value=None,
        attributes={"order_value": 100},
    )
    assert allowed.effect is Effect.ALLOW


def test_approval_actions_still_wins_over_conditions() -> None:
    # Unconditional ("always") approval is a different, simpler concept than a
    # condition -- it must still short-circuit before conditions are checked.
    policy = ToolPolicy(
        enabled=True,
        modify=True,
        approval_actions=frozenset({"modify"}),
        conditions=(Condition(attribute="order_value", operator=">", value=999_999),),
    )
    d = authorize_tool_call(
        policies=_policies(odoo=policy),
        connection_key="odoo",
        right="modify",
        value=None,
        attributes={"order_value": 1},
    )
    assert d.effect is Effect.REQUIRE_APPROVAL
    assert "modify" in d.reason


def test_a_policy_with_no_conditions_is_unaffected() -> None:
    # Full regression no-op: legacy approval_eur-only policies behave exactly
    # as before when `conditions`/`attributes` are simply absent.
    policy = ToolPolicy(enabled=True, modify=True, approval_eur=2500)
    d = authorize_tool_call(
        policies=_policies(odoo=policy), connection_key="odoo", right="modify", value=100.0
    )
    assert d.effect is Effect.ALLOW


def test_decision_from_a_condition_carries_the_condition_reason() -> None:
    condition = Condition(attribute="discount_percent", operator=">", value=20, then=Effect.DENY)
    decision = evaluate_conditions([condition], {"discount_percent": 35})
    assert isinstance(decision, Decision)
    assert decision.effect is Effect.DENY
    assert "discount_percent" in decision.reason and "20" in decision.reason
