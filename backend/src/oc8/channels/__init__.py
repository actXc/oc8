"""Approval channels (§5.6): an approval reaches the approver where they already are.

Core defines the seam and owns the record. Which messengers exist is a plugin
question -- nothing in this package names one.
"""

from oc8.channels.notice import (
    CLASSIFICATIONS,
    ApprovalNotice,
    ChannelCapabilities,
    ChannelDecision,
    ChannelLink,
    NoticeOption,
    outranks,
)

__all__ = [
    "CLASSIFICATIONS",
    "ApprovalNotice",
    "ChannelCapabilities",
    "ChannelDecision",
    "ChannelLink",
    "NoticeOption",
    "outranks",
]
