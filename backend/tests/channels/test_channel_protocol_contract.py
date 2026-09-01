"""The `ApprovalChannel` Protocol is the contract every channel plugin is
written against, so its signatures have to say what core actually does.

`parse_inbound` grew a fourth answer -- `ChannelFreeText`, routed to the tenant
Assistant (design doc 2026-08-31-unified-assistant-telegram-chat) -- and for a
while the Protocol still said the only legal answers were
`ChannelDecision | ChannelLink | None`, with a docstring telling plugin authors
free text "must" return None. A plugin author reading that would have written
exactly the behaviour this feature replaced, and a type checker would have
flagged the correct implementation as the wrong one.
"""

from __future__ import annotations

import typing

from oc8.channels.base import ApprovalChannel
from oc8.channels.notice import ChannelDecision, ChannelFreeText, ChannelLink


def test_parse_inbound_admits_free_text() -> None:
    hints = typing.get_type_hints(ApprovalChannel.parse_inbound)
    assert set(typing.get_args(hints["return"])) == {
        ChannelDecision,
        ChannelLink,
        ChannelFreeText,
        type(None),
    }


def test_the_docstring_no_longer_forbids_free_text() -> None:
    """The prose is the part a plugin author actually reads. It used to name
    a person typing "hallo" at the bot as an example of something to drop."""
    doc = ApprovalChannel.parse_inbound.__doc__ or ""
    assert "ChannelFreeText" in doc
    assert "hallo" not in doc
