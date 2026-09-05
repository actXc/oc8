"""Task 4: the real `plugins/odoo_mcp/guardrails/` folder is the reference
library (design §7) -- 20-30 documented ERP guardrails for the odoo_mcp tool
pack, one `kind = "library"` file per entry since the package restructure.
These tests assemble the real plugin off disk through `find_plugin` -- the same
path production uses -- and assert on its content directly, so editing the TOML
without editing this test fails (design §8)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from oc8.authz.pdp import (
    RIGHTS,
    Decision,
    Effect,
    ToolPolicy,
    authorize_tool,
    authorize_tool_call,
    effective_tool_policies,
)
from oc8.capas.discovery import DiscoveredPlugin, find_plugin
from oc8.capas.guardrails import Guardrail, GuardrailLibrary
from oc8.capas.i18n import translations_for
from oc8.capas.manifest import GuardrailPreset, parse_manifest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGINS_DIR = _REPO_ROOT / "capas"
#: Where the entries now live, for assertion messages. The presets are
#: `kind = "preset"` files in the SAME folder; discovery tells the two apart.
_GUARDRAILS_DIR = _PLUGINS_DIR / "odoo_mcp" / "guardrails"

# odoo_mcp's real tool classification (tool_pack.toml scopes): no tool is
# `write`, so every guardrail's `write` must be False.
_SEND_TOOLS = {"create_record", "update_record", "delete_record", "post_message"}
_READ_TOOLS = {
    "search_records",
    "get_record",
    "list_models",
    "list_resource_templates",
    "aggregate_records",
}


def _discovered() -> DiscoveredPlugin:
    """The real odoo_mcp plugin, assembled from plugin.toml + tool_pack.toml +
    guardrails/*.toml + setup/*.toml exactly as production assembles it."""
    found = find_plugin("odoo_mcp", [str(_PLUGINS_DIR)])
    assert found is not None, "odoo_mcp plugin not found"
    assert found.valid, found.error
    return found


def _library() -> GuardrailLibrary:
    lib = _discovered().guardrail_library
    assert lib is not None, f"expected {_GUARDRAILS_DIR} to ship kind = 'library' entries"
    return lib


def test_entry_count_is_between_20_and_30() -> None:
    lib = _library()
    assert 20 <= len(lib.guardrail) <= 30


def test_use_case_coverage_includes_every_required_group() -> None:
    lib = _library()
    use_cases = {g.use_case for g in lib.guardrail}
    for required in ("sales", "helpdesk", "purchasing", "finance", "inventory"):
        assert required in use_cases, f"missing use_case {required!r}"
    # A cross-cutting group (read-only / internal-only / never-deletes across
    # domains) is required by spec §7 in addition to the five above.
    cross_cutting = {
        uc
        for uc in use_cases
        if uc
        not in {
            "sales",
            "helpdesk",
            "purchasing",
            "finance",
            "inventory",
        }
    }
    assert cross_cutting, "expected at least one cross-cutting use_case group"


def test_all_keys_are_unique() -> None:
    # Already enforced by the model (duplicate keys raise); asserted again
    # here at the fixture level as a canary against a future model change.
    lib = _library()
    keys = [g.key for g in lib.guardrail]
    assert len(keys) == len(set(keys))


def test_no_tool_is_write_so_every_guardrail_write_is_false() -> None:
    # odoo_mcp's connection scopes classify every one of its 9 tools as
    # read or send, never write (tool_pack.toml). Granting `write` here would
    # confer nothing while implying an entitlement that does not exist.
    lib = _library()
    for g in lib.guardrail:
        assert g.write is False, f"{g.key} sets modify=True but odoo_mcp has no write tool"


def test_helpdesk_entry_gates_only_post_message_and_keeps_send_true() -> None:
    # The tool-name case unlocked by Task 1: "may reply to tickets, but
    # external communication needs approval" -- expressed by naming the tool
    # in approval_actions, NOT by gating all of `send`.
    lib = _library()
    matches = [
        g
        for g in lib.guardrail
        if g.use_case == "helpdesk" and g.approval_actions == frozenset({"post_message"})
    ]
    assert matches, "expected a helpdesk entry with approval_actions == {'post_message'}"
    entry = matches[0]
    assert entry.send is True, "gating post_message must not also gate all of send"


def test_an_entry_demonstrates_the_autonomous_with_limit_lesson() -> None:
    # A euro-threshold entry whose `only` excludes delete_record: a deletion
    # carries no amount, so a threshold can never gate one -- the tool must
    # be withheld outright instead (the autonomous_with_limit preset).
    lib = _library()
    matches = [
        g
        for g in lib.guardrail
        if g.approval_eur is not None and g.only and "delete_record" not in g.only
    ]
    assert matches, "expected a euro-threshold entry whose only excludes delete_record"


# Entries allowed to set approval_eur without excluding delete_record from
# `only`. Kept to exactly the one entry spec §4/the brief pin verbatim as the
# design doc's own worked example -- every other threshold entry must close
# the gap (fix round 1, finding #5/#6). Adding a key here is a deliberate,
# reviewed exemption, not a way to silence this test.
_THRESHOLD_ENTRIES_EXEMPT_FROM_DELETE_EXCLUSION = frozenset({"quote_approval_threshold"})


def test_every_threshold_entry_excludes_delete_record_except_the_pinned_example() -> None:
    # Spec §7: "Every threshold-based entry in this library needs the same
    # scrutiny" as autonomous_with_limit. Unlike the "at least one" test
    # above, this closes the gap that let a threshold entry with an open
    # `only` ship silently: for every entry with approval_eur set, either
    # `only` excludes delete_record, or the key is an explicit, reviewed
    # exemption.
    lib = _library()
    for g in lib.guardrail:
        if g.approval_eur is None:
            continue
        if g.key in _THRESHOLD_ENTRIES_EXEMPT_FROM_DELETE_EXCLUSION:
            continue
        assert g.only and "delete_record" not in g.only, (
            f"{g.key} sets approval_eur={g.approval_eur} but does not exclude delete_record "
            "from `only` -- a euro threshold can never gate a deletion, which carries no "
            "amount (design §7's autonomous_with_limit lesson). Either exclude delete_record "
            "from `only`, or add the key to _THRESHOLD_ENTRIES_EXEMPT_FROM_DELETE_EXCLUSION "
            "with a reason."
        )


def test_every_approval_eur_entry_has_a_matching_adjustable_block() -> None:
    lib = _library()
    eur_entries = [g for g in lib.guardrail if g.approval_eur is not None]
    assert eur_entries, "expected at least one entry with approval_eur set"
    for g in eur_entries:
        fields = {adj.field for adj in g.adjustable}
        assert "approval_eur" in fields, f"{g.key} sets approval_eur but has no matching adjustable"


def test_every_entry_read_and_send_only_name_real_odoo_mcp_tools() -> None:
    lib = _library()
    for g in lib.guardrail:
        for tool in g.only:
            assert tool in _READ_TOOLS | _SEND_TOOLS, f"{g.key}.only names unknown tool {tool!r}"
        for action in g.approval_actions:
            assert action in _READ_TOOLS | _SEND_TOOLS | {"send", "read", "write"}, (
                f"{g.key}.approval_actions names unrecognised {action!r}"
            )


def test_every_entry_has_bilingual_nonempty_prose() -> None:
    # `label_de`/`summary_de` no longer live on the model (capa-i18n design):
    # every library entry's German prose now has to actually resolve out of
    # odoo_mcp's `i18n/de.po` catalog instead.
    i18n = _discovered().i18n
    lib = _library()
    for g in lib.guardrail:
        for field in (g.label, g.summary):
            assert field and field.strip()
        assert translations_for(i18n, g.label).get("de", "").strip()
        assert translations_for(i18n, g.summary).get("de", "").strip()


# --- Specific entries, asserted by exact key + field values (design §8: "so
# editing the library without editing the test fails; these values are a
# security posture, not configuration"). ---


def test_quote_approval_threshold_matches_the_designs_own_example() -> None:
    lib = _library()
    entries = {g.key: g for g in lib.guardrail}
    assert "quote_approval_threshold" in entries
    g = entries["quote_approval_threshold"]
    assert g.use_case == "sales"
    assert g.read is True
    assert g.write is False
    assert g.send is True
    assert g.approval_eur == 3000
    assert g.approval_actions == frozenset()
    assert g.only == ()
    assert any(adj.field == "approval_eur" for adj in g.adjustable)


def test_sales_autonomous_with_limit_excludes_delete_record() -> None:
    lib = _library()
    entries = {g.key: g for g in lib.guardrail}
    assert "sales_autonomous_with_limit" in entries
    g = entries["sales_autonomous_with_limit"]
    assert g.use_case == "sales"
    assert g.send is True
    assert g.approval_eur == 1000
    assert "delete_record" not in g.only
    assert set(g.only) == _READ_TOOLS | {"create_record", "update_record", "post_message"}


def test_helpdesk_reply_needs_approval_exact_values() -> None:
    lib = _library()
    entries = {g.key: g for g in lib.guardrail}
    assert "helpdesk_reply_needs_approval" in entries
    g = entries["helpdesk_reply_needs_approval"]
    assert g.use_case == "helpdesk"
    assert g.read is True
    assert g.write is False
    assert g.send is True
    assert g.approval_actions == frozenset({"post_message"})
    assert g.approval_eur is None


# --- End-to-end: real library entries -> real PDP decisions (Task 8) ---
#
# Everything above asserts on PARSED FIELDS. That is one half of the promise:
# it proves the TOML says what it means to say, and nothing at all about what
# happens when an agent actually calls a tool. The tests below close that loop
# for a representative slice -- one euro-threshold entry, one entry gating a
# tool BY NAME, one entry withholding a tool outright -- by running the parsed
# entry through the SAME two steps production does and asserting the decision
# the entry's own `summary`/`summary_de` claims.
#
# The gate exercised is `authorize_tool_call`, deliberately: it is the one
# `agent/engine.py` and `api/mcp_gateway.py` reach for every real tool call.
# `authorize_tool` answers a related question over a raw frame/narrowing pair
# and has no production callers; asserting only against it is exactly how
# `approval_actions` once shipped with no runtime effect while the prose
# promised one.

_CONNECTION_KEY = "odoo"


def _presets() -> list[GuardrailPreset]:
    """odoo_mcp's five `kind = "preset"` guardrails, through the real manifest
    parser -- assembled onto the tool-pack connection by discovery, the same
    way production reads them.

    The fallback for any connection without a library (design §6) -- read at
    least as often as the library, and resolving to the same `ToolPolicy`
    shape, so the honesty checks below apply to both.
    """
    raw = _discovered().manifest
    assert raw is not None
    manifest = parse_manifest(raw)
    assert manifest.tool_pack is not None, "odoo_mcp is a tool pack"
    presets = [p for c in manifest.tool_pack.connections for p in c.guardrail_presets]
    assert presets, "expected odoo_mcp's guardrails/ to still ship kind = 'preset' entries"
    return presets


def _entry(key: str) -> Guardrail:
    entries = {g.key: g for g in _library().guardrail}
    assert key in entries, f"no guardrail {key!r} in {_GUARDRAILS_DIR}"
    return entries[key]


def _policies(key: str) -> dict[str, ToolPolicy]:
    """Resolve one library entry the way the runtime resolves a real frame.

    Applying a guardrail writes its fields into
    `department.frame["tools"][<connection>]` as an ordinary tool policy
    (`plugins/guardrails.py`'s module docstring) -- the PDP never learns a
    guardrail library exists. The runtime then resolves that frame through
    `effective_tool_policies` once per run and hands the resulting mapping to
    `authorize_tool_call` per call. Both steps are walked here rather than
    building a `ToolPolicy` by hand, so a change to either one surfaces.
    """
    g = _entry(key)
    return _policies_for(
        read=g.read,
        write=g.write,
        send=g.send,
        approval_eur=g.approval_eur,
        approval_actions=sorted(g.approval_actions),
        only=list(g.only),
    )


def _policies_for(
    *,
    read: bool,
    write: bool,
    send: bool,
    approval_eur: float | None,
    approval_actions: list[str],
    only: list[str],
) -> dict[str, ToolPolicy]:
    """The frame-JSON -> `effective_tool_policies` half of the path above.

    Split out because a `kind = "preset"` guardrail (`GuardrailPreset`) and a
    `kind = "library"` one (`Guardrail`) are different models that resolve to
    the very same policy -- that is the whole design -- so both can be checked
    against the real gate through one construction.
    """
    frame: dict[str, Any] = {
        "tools": {
            _CONNECTION_KEY: {
                "enabled": True,
                "read": read,
                "write": write,
                "send": send,
                "approval_eur": approval_eur,
                "approval_actions": approval_actions,
                "only": only,
            }
        }
    }
    return effective_tool_policies(frame, {})


def _decide(key: str, *, right: str, tool: str, value: float | None = None) -> Decision:
    return authorize_tool_call(
        policies=_policies(key),
        connection_key=_CONNECTION_KEY,
        right=right,
        value=value,
        tool=tool,
    )


def test_integration_quote_approval_threshold_decides_as_its_summary_claims() -> None:
    # "Creates and maintains quotes on its own; above the configured amount a
    # human decides" -- and, disclosed in the same summary, "delete_record and
    # post_message stay reachable ungated". Both halves asserted: the entry is
    # the design doc's pinned worked example, so its gap is documented rather
    # than closed, and a test that only checked the flattering half would let
    # that disclosure quietly become false.
    key = "quote_approval_threshold"
    assert _entry(key).approval_eur == 3000

    assert _decide(key, right="read", tool="search_records").effect is Effect.ALLOW
    assert _decide(key, right="send", tool="create_record", value=2999).effect is Effect.ALLOW

    at_threshold = _decide(key, right="send", tool="create_record", value=3000)
    assert at_threshold.effect is Effect.REQUIRE_APPROVAL
    assert "3000" in at_threshold.reason
    assert _decide(key, right="send", tool="create_record", value=9000).effect is (
        Effect.REQUIRE_APPROVAL
    )

    # The documented gap, verified as real rather than assumed: `only` is empty,
    # so both tools are offered, and neither call carries an amount the €3000
    # threshold could catch (an unreadable value counts as zero).
    assert _decide(key, right="send", tool="delete_record").effect is Effect.ALLOW
    assert _decide(key, right="send", tool="post_message").effect is Effect.ALLOW


def test_integration_sales_autonomous_with_limit_decides_as_its_summary_claims() -> None:
    # "Above the configured amount a human decides. Deleting is excluded,
    # because a euro threshold can never catch a deletion, which carries no
    # amount." The second sentence is the whole reason this entry exists next
    # to the one above, so it is asserted as a DENY, not merely as a missing
    # name in `only`.
    key = "sales_autonomous_with_limit"
    assert _entry(key).approval_eur == 1000

    assert _decide(key, right="read", tool="search_records").effect is Effect.ALLOW
    assert _decide(key, right="send", tool="create_record", value=999).effect is Effect.ALLOW
    assert _decide(key, right="send", tool="update_record", value=1000).effect is (
        Effect.REQUIRE_APPROVAL
    )

    denied = _decide(key, right="send", tool="delete_record")
    assert denied.effect is Effect.DENY
    assert "delete_record" in denied.reason


def test_integration_helpdesk_reply_needs_approval_gates_only_that_one_tool() -> None:
    # The tool-name case: "sending a message to a person (post_message) needs
    # approval" while "stage, assignment or status still change without
    # approval". Strictness AND precision -- gating the whole `send` right
    # instead would have made the second sentence false.
    key = "helpdesk_reply_needs_approval"
    assert _entry(key).approval_actions == frozenset({"post_message"})

    gated = _decide(key, right="send", tool="post_message")
    assert gated.effect is Effect.REQUIRE_APPROVAL
    assert "post_message" in gated.reason

    # Same right, same connection, different tool: must not be swept up.
    assert _decide(key, right="send", tool="update_record").effect is Effect.ALLOW
    assert _decide(key, right="send", tool="create_record").effect is Effect.ALLOW
    assert _decide(key, right="read", tool="search_records").effect is Effect.ALLOW

    # And the gap this entry discloses in its own summary ("a ticket can be
    # deleted without review while only sending is filtered"), asserted so the
    # disclosure cannot silently stop matching the behaviour.
    assert _decide(key, right="send", tool="delete_record").effect is Effect.ALLOW


def test_integration_cross_internal_only_withholds_the_outward_tool() -> None:
    # "post_message and delete_record are withheld" -- withheld, not gated:
    # nothing about this entry sends anything to a human for approval, so a
    # REQUIRE_APPROVAL here would be as wrong as an ALLOW.
    key = "cross_internal_only_never_customer_facing"
    entry = _entry(key)
    assert entry.approval_eur is None
    assert entry.approval_actions == frozenset()

    for tool in ("post_message", "delete_record"):
        denied = _decide(key, right="send", tool=tool)
        assert denied.effect is Effect.DENY, f"{tool} must be unreachable, got {denied.effect}"
        assert tool in denied.reason

    assert _decide(key, right="send", tool="create_record").effect is Effect.ALLOW
    assert _decide(key, right="send", tool="update_record").effect is Effect.ALLOW
    assert _decide(key, right="read", tool="get_record").effect is Effect.ALLOW


def test_integration_every_threshold_entrys_boundary_is_inclusive() -> None:
    # The PDP compares `value >= threshold`, so the configured amount ITSELF
    # needs approval. Found by this end-to-end pass, in SIX of the six
    # threshold entries: two said "übersteigt"/"exceeds" outright, and four
    # more said "above the configured amount", which excludes the amount in
    # English while their German half ("ab dem eingestellten Betrag")
    # includes it -- the same defect, visible in one language only. All six
    # now describe the boundary the PDP actually applies; this asserts that
    # behaviour for every threshold entry, not just the ones that were wrong.
    thresholds = [g for g in _library().guardrail if g.approval_eur is not None]
    assert thresholds, "expected at least one entry with approval_eur set"
    for g in thresholds:
        threshold = g.approval_eur
        assert threshold is not None
        at = _decide(g.key, right="send", tool="create_record", value=threshold)
        assert at.effect is Effect.REQUIRE_APPROVAL, (
            f"{g.key}: a call worth exactly €{threshold} must need approval"
        )
        below = _decide(g.key, right="send", tool="create_record", value=threshold - 1)
        assert below.effect is Effect.ALLOW, f"{g.key}: €{threshold - 1} must not need approval"


#: Phrasings that describe the EXCLUSIVE boundary (value > threshold). The
#: PDP's is inclusive, so a threshold description using one of these is off by
#: the amount itself -- the exact value an operator typed in.
#:
#: "above the configured amount" is on this list, and that is the point. It
#: was the wording of four entries here AND of the design doc's own pinned
#: worked example, which is why nothing caught it: the German half of all
#: four ("ab dem eingestellten Betrag") is unambiguously inclusive, so the
#: mismatch existed only in the English. Being the example's wording made it
#: more worth fixing, not less -- an inaccuracy in the piece everyone copies
#: propagates. The design doc was corrected alongside the entries.
#:
#: A blocklist catches the phrasings we have actually seen, not every
#: possible one. `test_integration_every_threshold_entrys_boundary_is_inclusive`
#: is the real anchor; this is the half of the promise no decision can check.
_EXCLUSIVE_BOUNDARY_PHRASES = (
    "übersteigt",
    "exceeds",
    "oberhalb",
    "above the configured",
    "more than",
)

#: The inclusive wording these descriptions are meant to use. Stripped before
#: scanning, because it CONTAINS "above the configured" while meaning the
#: opposite of it.
_INCLUSIVE_PHRASE = "at or above"

_INCLUSIVE_HINT = (
    "The PDP requires approval at exactly that amount too -- say 'ab dem eingestellten Betrag' "
    "/ 'at or above the configured amount' instead."
)


def _exclusive_boundary_phrase(prose: str) -> str | None:
    """The first exclusive-boundary phrase in `prose`, or None if it is clean."""
    haystack = prose.lower().replace(_INCLUSIVE_PHRASE, "")
    return next((p for p in _EXCLUSIVE_BOUNDARY_PHRASES if p in haystack), None)


def test_no_threshold_entry_describes_an_exclusive_boundary() -> None:
    # The half of the mismatch above that no decision can catch: a summary
    # promising one boundary while the PDP applies another is wrong even when
    # every assertion about behaviour passes.
    i18n = _discovered().i18n
    for g in _library().guardrail:
        if g.approval_eur is None:
            continue
        de_summary = translations_for(i18n, g.summary).get("de", "")
        for prose in (g.summary, de_summary):
            found = _exclusive_boundary_phrase(prose)
            assert found is None, (
                f"guardrail {g.key} (€{g.approval_eur}) describes its threshold with "
                f"{found!r}, an exclusive boundary. {_INCLUSIVE_HINT}"
            )


def test_no_plugin_toml_preset_describes_an_exclusive_boundary() -> None:
    # The same check for the presets. odoo_mcp's five `kind = "preset"`
    # guardrails are the fallback for any connection without a library
    # (design §6), so they are read at least as often as the library is -- and
    # `autonomous_with_limit` shipped with its two halves contradicting each
    # other: "ab 1000 €" (inclusive, correct) beside "anything worth more than
    # EUR 1,000" (exclusive, wrong). Nothing that read only the
    # `kind = "library"` entries could have seen it.
    i18n = _discovered().i18n
    checked = 0
    for p in _presets():
        if p.approval_eur is None:
            continue
        checked += 1
        de_summary = translations_for(i18n, p.summary).get("de", "")
        for prose in (p.summary, de_summary):
            found = _exclusive_boundary_phrase(prose)
            assert found is None, (
                f"preset {p.key} (€{p.approval_eur}) describes its threshold with "
                f"{found!r}, an exclusive boundary. {_INCLUSIVE_HINT}"
            )
    assert checked, "expected at least one preset with a euro threshold to check"


def test_integration_every_plugin_toml_presets_boundary_is_inclusive() -> None:
    # The behavioural anchor for the prose fixed above, so `autonomous_with_limit`
    # cannot drift back: a call worth exactly its €1000 needs a human, and one
    # euro under does not.
    checked = 0
    for p in _presets():
        if p.approval_eur is None:
            continue
        checked += 1
        policies = _policies_for(
            read=p.read,
            write=p.write,
            send=p.send,
            approval_eur=p.approval_eur,
            approval_actions=list(p.approval_actions),
            only=list(p.only),
        )

        def decide(value: float, policies: dict[str, ToolPolicy] = policies) -> Decision:
            return authorize_tool_call(
                policies=policies,
                connection_key=_CONNECTION_KEY,
                right="send",
                value=value,
                tool="create_record",
            )

        assert decide(p.approval_eur).effect is Effect.REQUIRE_APPROVAL, (
            f"preset {p.key}: a call worth exactly €{p.approval_eur} must need approval"
        )
        assert decide(p.approval_eur - 1).effect is Effect.ALLOW, (
            f"preset {p.key}: €{p.approval_eur - 1} must not need approval"
        )
    assert checked, "expected at least one preset with a euro threshold to check"


def test_no_odoo_mcp_tool_name_collides_with_a_right() -> None:
    # Carried here explicitly from Task 0's review and Task 2's ledger note:
    # `approval_actions` matches a member against BOTH the rights vocabulary
    # and the tool names, so a plugin tool literally called "send" would
    # silently gate every send-capable tool instead of only itself. Neither
    # the guardrails/ parser nor the departments endpoint can check that
    # (neither has the plugin's tool list); here, where the real list is
    # known, it is checkable -- and clean.
    for tool in _READ_TOOLS | _SEND_TOOLS:
        assert tool not in RIGHTS, (
            f"odoo_mcp exposes a tool named {tool!r}, which collides with the right of the "
            "same name -- naming it in a guardrail's approval_actions would gate every tool "
            "granting that right, not just this one."
        )


def test_integration_no_entry_grants_the_write_right_at_decision_time() -> None:
    # The field-level twin of this assertion is
    # `test_no_tool_is_write_so_every_guardrail_write_is_false`. Here it is
    # the decision that is checked: odoo_mcp classifies no tool as `write`, so
    # a `write` call against any entry in the library must be denied outright.
    for g in _library().guardrail:
        decision = authorize_tool_call(
            policies=_policies(g.key),
            connection_key=_CONNECTION_KEY,
            right="write",
            value=None,
            tool="update_record",
        )
        assert decision.effect is Effect.DENY, f"{g.key} granted a write decision"


def test_integration_the_raw_frame_helper_cannot_see_a_tool_name() -> None:
    # Why every assertion above goes through `authorize_tool_call`, pinned as
    # a test rather than left as a comment. `authorize_tool` conflates the
    # policy lookup key with the tool identity in its single `tool_key`
    # argument, so the helpdesk entry -- keyed by CONNECTION, gating a TOOL --
    # cannot be expressed to it at all: it never sees "post_message" and
    # allows the call. Nothing is broken; it is answering a different
    # question. Wiring a runtime path to it would be, which is what this
    # asserts against.
    g = _entry("helpdesk_reply_needs_approval")
    frame: dict[str, Any] = {
        "tools": {
            _CONNECTION_KEY: {
                "enabled": True,
                "read": g.read,
                "send": g.send,
                "approval_actions": sorted(g.approval_actions),
            }
        }
    }
    assert authorize_tool(frame, {}, tool_key=_CONNECTION_KEY, action="modify").effect is Effect.ALLOW
    # The live gate, given the same entry and the tool the call actually names:
    assert _decide("helpdesk_reply_needs_approval", right="send", tool="post_message").effect is (
        Effect.REQUIRE_APPROVAL
    )
