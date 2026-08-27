"""Every right has WORDS, and no two of them are the same words.

`describe()` falls back to the permission string when a permission has no prose,
which is the right behaviour at runtime -- the screen that explains refusals must
not 500 because somebody added a permission -- and it is exactly what makes the
endpoint test unable to notice the gap: `entry["label"]` is non-empty either way.
So the completeness is asserted against the SOURCE dict here, not against the
response.

The uniqueness assertion is the point of the module. The screen renders
`permission.split(":")[1]` today, so `supervision:manage`, `handoff:manage`,
`contract:manage` and `flow:manage` are four different rights that all read as
"manage" to the person deciding whether to tick them -- and a catalogue whose
labels were composed from the resource and the action would reproduce that
faithfully in two languages. Distinct labels are the whole deliverable.
"""

from __future__ import annotations

from oc8.authz.catalog import _PROSE, catalogue, describe
from oc8.authz.permissions import (
    ALL_PERMISSIONS,
    DELEGATABLE_PERMISSIONS,
    NEVER_DELEGATABLE,
    NOT_YET_DELEGATABLE,
    delegation_refusal,
)


def test_every_permission_has_hand_written_prose() -> None:
    """A permission added without words fails the build here.

    Not at the endpoint: `describe()` falls back to the string itself, so the
    response is well-formed for a permission nobody described and the screen
    simply shows `supervision:manage` where a sentence should be. This is the
    only assertion that can tell the difference.
    """
    missing = sorted(ALL_PERMISSIONS - set(_PROSE))
    assert not missing, (
        f"{len(missing)} permission(s) have no label or description: {missing}. "
        "Add them to authz/catalog.py; the screen renders the raw string until "
        "somebody does, and nothing else notices."
    )
    stale = sorted(set(_PROSE) - ALL_PERMISSIONS)
    assert not stale, f"prose for permissions that no longer exist: {stale}"


def test_no_two_rights_read_as_the_same_thing() -> None:
    """Distinct labels, in both languages, and that is the deliverable.

    Four `:manage` permissions in this catalogue are near-synonyms in English --
    supervision, handoff, contract, flow -- and the entire reason this module is
    hand-written rather than composed from the resource and the action is that
    composition renders those four identically to a layperson. If two labels
    collide, the administrator's form has two boxes he cannot tell apart, and he
    will tick the wrong one exactly as often as the right one.
    """
    for language, index in (("German", 0), ("English", 2)):
        seen: dict[str, str] = {}
        collisions: list[str] = []
        for permission, prose in _PROSE.items():
            label = prose[index].strip().lower()
            if label in seen:
                collisions.append(f"{seen[label]} and {permission} are both {prose[index]!r}")
            seen[label] = permission
        assert not collisions, f"{language}: " + "; ".join(collisions)


def test_no_label_is_the_permission_string_wearing_a_hat() -> None:
    """A label containing a colon is a label somebody pasted rather than wrote.

    It is also how the fallback in `describe()` would look if it were ever
    committed into the dict itself -- at which point the completeness test above
    passes and the screen is back to rendering identifiers at people.
    """
    pasted = sorted(
        p
        for p, prose in _PROSE.items()
        if ":" in prose[0] or ":" in prose[2] or prose[0].strip() == p or prose[2].strip() == p
    )
    assert not pasted, f"these read as identifiers rather than as words: {pasted}"

    thin = sorted(
        p
        for p, prose in _PROSE.items()
        # A description is a SENTENCE. Anything short enough to be a second label
        # is one, and a form with two labels and no explanation is what this
        # module exists to replace.
        if len(prose[1].strip()) < 25 or len(prose[3].strip()) < 25
    )
    assert not thin, f"these have a label where a sentence should be: {thin}"


def test_the_catalogue_never_invents_a_refusal_and_never_omits_one() -> None:
    """The reason is `delegation_refusal`'s, verbatim.

    One string, two readers: the disabled checkbox on the form and the 422 from
    `POST /roles`. A catalogue with its own, gentler wording would put the screen
    and the refusal into a disagreement that only the person being refused can
    see -- and it would drift, because only one of the two is next to the rule.
    """
    entries = {info.permission: info for info in catalogue()}
    assert set(entries) == set(ALL_PERMISSIONS)

    refused = set(NOT_YET_DELEGATABLE) | set(NEVER_DELEGATABLE)
    for permission, info in entries.items():
        assert info.delegatable is (permission in DELEGATABLE_PERMISSIONS), permission
        assert info.reason == (delegation_refusal(permission) or ""), permission
        if permission in refused:
            assert info.reason, f"{permission} is refused and says nothing"
        else:
            assert not info.reason, (
                f"{permission} may be granted and carries a refusal reason anyway"
            )


def test_a_permission_nobody_described_still_renders() -> None:
    """The fallback, asserted rather than assumed.

    This is the diagnostic screen: it explains refusals, and it must not be the
    page that breaks when somebody adds a permission and forgets the words. The
    test above is what makes the omission loud in CI; this is what makes it
    harmless in production.
    """
    invented = describe("newresource:view")
    assert invented.label == "newresource:view"
    assert invented.delegatable is False
    assert invented.reason == ""
