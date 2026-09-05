from __future__ import annotations

from oc8.authz.pdp import (
    Effect,
    ToolPolicy,
    authorize_tool,
    authorize_tool_call,
    required_right,
)


def _policies(**kw: ToolPolicy) -> dict[str, ToolPolicy]:
    return dict(kw)


def test_unclassified_tool_requires_modify() -> None:
    # Fail-closed: an unknown tool is treated as the more dangerous case.
    assert required_right("anything", {"read": ["fs_read"]}) == "modify"


def test_scopes_classify_read_and_modify() -> None:
    scopes = {"read": ["fs_read"], "modify": ["send_email"]}
    assert required_right("fs_read", scopes) == "read"
    assert required_right("send_email", scopes) == "modify"


def test_missing_scopes_means_everything_is_modify() -> None:
    assert required_right("fs_read", None) == "modify"
    assert required_right("fs_read", {}) == "modify"


def test_no_frame_entry_denies() -> None:
    d = authorize_tool_call(
        policies=_policies(odoo=ToolPolicy(enabled=True, read=True)),
        connection_key="github",
        right="read",
        value=None,
    )
    assert d.effect is Effect.DENY
    assert "github" in d.reason


def test_unknown_connection_key_denies() -> None:
    d = authorize_tool_call(
        policies=_policies(odoo=ToolPolicy(enabled=True, read=True)),
        connection_key=None,
        right="read",
        value=None,
    )
    assert d.effect is Effect.DENY


def test_disabled_entry_denies() -> None:
    d = authorize_tool_call(
        policies=_policies(odoo=ToolPolicy(enabled=False, read=True, modify=True)),
        connection_key="odoo",
        right="read",
        value=None,
    )
    assert d.effect is Effect.DENY
    assert "disabled" in d.reason


def test_missing_right_denies_and_names_it() -> None:
    d = authorize_tool_call(
        policies=_policies(odoo=ToolPolicy(enabled=True, read=True, modify=False)),
        connection_key="odoo",
        right="modify",
        value=None,
    )
    assert d.effect is Effect.DENY
    assert "modify" in d.reason


def test_granted_right_allows() -> None:
    d = authorize_tool_call(
        policies=_policies(odoo=ToolPolicy(enabled=True, read=True)),
        connection_key="odoo",
        right="read",
        value=None,
    )
    assert d.effect is Effect.ALLOW


def test_value_over_policy_threshold_requires_approval() -> None:
    d = authorize_tool_call(
        policies=_policies(odoo=ToolPolicy(enabled=True, modify=True, approval_eur=2500)),
        connection_key="odoo",
        right="modify",
        value=3000.0,
    )
    assert d.effect is Effect.REQUIRE_APPROVAL
    assert "2500" in d.reason


def test_value_under_every_threshold_allows() -> None:
    d = authorize_tool_call(
        policies=_policies(odoo=ToolPolicy(enabled=True, modify=True, approval_eur=2500)),
        connection_key="odoo",
        right="modify",
        value=100.0,
        extra_thresholds=(5000.0,),
    )
    assert d.effect is Effect.ALLOW


def test_the_strictest_threshold_governs() -> None:
    # Policy says 2500, a skill guardrail says 100 -> 100 wins.
    d = authorize_tool_call(
        policies=_policies(odoo=ToolPolicy(enabled=True, modify=True, approval_eur=2500)),
        connection_key="odoo",
        right="modify",
        value=500.0,
        extra_thresholds=(100.0, None),
    )
    assert d.effect is Effect.REQUIRE_APPROVAL
    assert "100" in d.reason


def test_none_thresholds_are_ignored() -> None:
    d = authorize_tool_call(
        policies=_policies(odoo=ToolPolicy(enabled=True, modify=True, approval_eur=None)),
        connection_key="odoo",
        right="modify",
        value=1_000_000.0,
        extra_thresholds=(None,),
    )
    assert d.effect is Effect.ALLOW


def test_non_rights_attribute_denies() -> None:
    # `right` must be a member of RIGHTS, not just any ToolPolicy attribute --
    # otherwise "approval_eur" (or any other field) could be smuggled in as a
    # right and bypass read/modify entirely.
    d = authorize_tool_call(
        policies=_policies(
            odoo=ToolPolicy(enabled=True, read=False, modify=False, approval_eur=100)
        ),
        connection_key="odoo",
        right="approval_eur",
        value=None,
    )
    assert d.effect is Effect.DENY


def test_unknown_right_denies() -> None:
    d = authorize_tool_call(
        policies=_policies(odoo=ToolPolicy(enabled=True, read=True, modify=True)),
        connection_key="odoo",
        right="admin",
        value=None,
    )
    assert d.effect is Effect.DENY


def test_scopes_modify_key_classifies_modify() -> None:
    # The tool appears in the modify bucket. The right is correctly classified
    # as "modify" based on RIGHTS order.
    scopes = {"read": ["fs_read"], "modify": ["fs_write", "send_email"]}
    assert required_right("fs_write", scopes) == "modify"


def test_coding_tools_are_classified() -> None:
    from oc8.coding.tools import CODING_FRAME_KEY, CODING_TOOL_RIGHTS

    assert CODING_FRAME_KEY == "coding"
    assert CODING_TOOL_RIGHTS["fs_read"] == "read"
    assert CODING_TOOL_RIGHTS["fs_write"] == "modify"
    assert CODING_TOOL_RIGHTS["shell_run"] == "modify"


def test_a_zero_threshold_gates_an_action_that_carries_no_value() -> None:
    """The only way to say "every outward action needs a human".

    Approval was value-based end to end: a call whose value cannot be read --
    a helpdesk reply, a status change, anything without a price -- passed
    unconditionally, whatever the frame said. So a department could not express
    "the agent may draft, a human sends", which is the policy most pilots want
    for the first customer-facing action. A threshold of €0 now means exactly
    that; `None` still means "no threshold at all".
    """
    d = authorize_tool_call(
        policies=_policies(odoo=ToolPolicy(enabled=True, modify=True, approval_eur=0)),
        connection_key="odoo",
        right="modify",
        value=None,
    )
    assert d.effect is Effect.REQUIRE_APPROVAL


def test_a_valueless_call_under_a_real_threshold_is_still_allowed() -> None:
    """The compatibility half: every existing frame carries a positive threshold,
    and a valueless call under one must keep passing untouched."""
    d = authorize_tool_call(
        policies=_policies(odoo=ToolPolicy(enabled=True, modify=True, approval_eur=3000)),
        connection_key="odoo",
        right="modify",
        value=None,
    )
    assert d.effect is Effect.ALLOW


def test_approval_actions_right_requires_approval_with_no_threshold_at_all() -> None:
    # No `approval_eur` set anywhere -- the old mechanism could not express
    # this, and neither could `authorize_tool_call` before it read
    # `approval_actions`.
    d = authorize_tool_call(
        policies=_policies(
            odoo=ToolPolicy(enabled=True, modify=True, approval_actions=frozenset({"modify"}))
        ),
        connection_key="odoo",
        right="modify",
        tool="post_message",
        value=None,
    )
    assert d.effect is Effect.REQUIRE_APPROVAL
    assert "modify" in d.reason


def test_approval_actions_tool_name_gates_only_that_tool() -> None:
    # "post_message" is listed by TOOL NAME, not by right. It must require
    # approval; "create_record", which shares the same "modify" right on the
    # same connection, must not.
    policy = ToolPolicy(enabled=True, modify=True, approval_actions=frozenset({"post_message"}))
    gated = authorize_tool_call(
        policies=_policies(odoo=policy),
        connection_key="odoo",
        right="modify",
        tool="post_message",
        value=None,
    )
    assert gated.effect is Effect.REQUIRE_APPROVAL
    assert "post_message" in gated.reason

    ungated = authorize_tool_call(
        policies=_policies(odoo=policy),
        connection_key="odoo",
        right="modify",
        tool="create_record",
        value=None,
    )
    assert ungated.effect is Effect.ALLOW


def test_approval_actions_wins_over_a_high_euro_threshold() -> None:
    # If the value were compared against the threshold, €1 would sail under
    # €100000 and ALLOW. It must not be compared at all.
    d = authorize_tool_call(
        policies=_policies(
            odoo=ToolPolicy(
                enabled=True,
                modify=True,
                approval_eur=100_000,
                approval_actions=frozenset({"modify"}),
            )
        ),
        connection_key="odoo",
        right="modify",
        tool="post_message",
        value=1.0,
    )
    assert d.effect is Effect.REQUIRE_APPROVAL
    assert "modify" in d.reason


def test_a_denied_right_stays_denied_even_if_listed_in_approval_actions() -> None:
    # `approval_actions` names "send", but this connection does not grant
    # `send` at all -- the DENY checks run first and there is nothing to
    # route to a human.
    d = authorize_tool_call(
        policies=_policies(
            odoo=ToolPolicy(enabled=True, modify=False, approval_actions=frozenset({"send"}))
        ),
        connection_key="odoo",
        right="modify",
        tool="post_message",
        value=None,
    )
    assert d.effect is Effect.DENY


def test_empty_approval_actions_is_a_full_regression_no_op() -> None:
    # A policy with no `approval_actions` at all must behave exactly as
    # before: euro threshold governs, nothing forces approval that the
    # threshold logic wouldn't have already required.
    below = authorize_tool_call(
        policies=_policies(odoo=ToolPolicy(enabled=True, modify=True, approval_eur=2500)),
        connection_key="odoo",
        right="modify",
        tool="post_message",
        value=100.0,
    )
    assert below.effect is Effect.ALLOW

    above = authorize_tool_call(
        policies=_policies(odoo=ToolPolicy(enabled=True, modify=True, approval_eur=2500)),
        connection_key="odoo",
        right="modify",
        tool="post_message",
        value=3000.0,
    )
    assert above.effect is Effect.REQUIRE_APPROVAL


def test_the_frame_path_gates_a_valueless_send_at_zero_too() -> None:
    """`authorize_tool` is the other policy entry point (frame + narrowing, used
    by the in-process toolset). The two must not disagree about what €0 means, or
    the same department behaves differently depending on which runtime executes it.
    """
    frame = {"tools": {"odoo": {"enabled": True, "read": True, "modify": True, "approval_eur": 0}}}
    gated = authorize_tool(frame, {}, tool_key="odoo", action="modify", value_eur=None)
    assert gated.effect is Effect.REQUIRE_APPROVAL

    frame_3000 = {
        "tools": {"odoo": {"enabled": True, "read": True, "modify": True, "approval_eur": 3000}}
    }
    assert (
        authorize_tool(frame_3000, {}, tool_key="odoo", action="modify", value_eur=None).effect
        is Effect.ALLOW
    )
