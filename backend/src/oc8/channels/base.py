"""What an approval channel plugin has to implement (§5.6).

Three verbs, and the third is the one people forget: a question left standing in
a chat after it has been answered somewhere else is how the same approval gets
answered twice.

Everything is best-effort from core's side. A messenger being unreachable must
never stop an approval from being raised -- the inbox is still there, and an
approval that failed to send is worse than useless if it also failed to exist.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from oc8.channels.notice import (
    ApprovalNotice,
    ChannelCapabilities,
    ChannelDecision,
    ChannelFreeText,
    ChannelLink,
)


@runtime_checkable
class ApprovalChannel(Protocol):
    """A place a human can be asked, and can answer from."""

    #: Stable across restarts: it is stored on every binding row and is what an
    #: inbound webhook is addressed to.
    channel_id: str

    def capabilities(self) -> ChannelCapabilities:
        """What this channel may carry and whether it may speak first."""
        ...

    async def deliver(self, notice: ApprovalNotice, *, external_id: str) -> str | None:
        """Show one approver the request. Returns a handle the platform gave the
        message (so `withdraw` can find it again), or None if there is none.

        Raising is allowed and expected -- the caller treats a failure to deliver
        as "this person was not told", logs it, and carries on with the others.
        """
        ...

    async def withdraw(
        self, notice: ApprovalNotice, *, external_id: str, handle: str | None, outcome: str
    ) -> None:
        """Say the question is closed: decided elsewhere, expired, or withdrawn.

        Without this the approval keeps sitting in somebody's chat looking open,
        and the second person to answer it gets told they were too late by a
        system that never bothered to tell them earlier.
        """
        ...

    def verify_inbound(self, *, headers: Mapping[str, str], body: bytes) -> bool:
        """Is this really from the platform?

        The webhook has no oc8 authentication and cannot have any -- the caller
        is Telegram or Meta, not a user. Each platform signs its calls its own
        way (a secret-token header, an HMAC over the body), so each plugin does
        its own check and core refuses anything that returns False BEFORE
        parsing, let alone acting.

        Must fail closed: a plugin that cannot verify returns False rather than
        trusting the call, because an unauthenticated webhook that reaches the
        decision path is an open door to approving anything.
        """
        ...

    def parse_inbound(
        self, update: Mapping[str, Any]
    ) -> ChannelDecision | ChannelLink | ChannelFreeText | None:
        """What the platform is telling us, in oc8's vocabulary, or None.

        Four answers, not three. A plain sentence a human typed at the bot is a
        `ChannelFreeText` -- `dispatch.process_inbound` routes it to the tenant
        Assistant (design doc 2026-08-31-unified-assistant-telegram-chat). Until
        that slice this had to be `None`, and this signature said so; a plugin
        still returning `None` for free text keeps working exactly as before, it
        just never reaches the Assistant.

        None for everything that is genuinely not one of ours -- a delivery
        receipt, an edited-message event, a platform's own housekeeping.
        Returning None is normal and must never be an error.
        """
        ...


#: How core asks a plugin for a channel. The mapping is the installation's own
#: config with any `secret_ref` already resolved to a value -- resolving secrets
#: is core's job, and a plugin that could read the secret store could read
#: another plugin's credentials.
ChannelFactory = Callable[[Mapping[str, str]], ApprovalChannel]
