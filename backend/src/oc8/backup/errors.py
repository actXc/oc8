"""Shared exception for the backup package (design doc: every failure mode
returns a specific, actionable message rather than a 500)."""

from __future__ import annotations


class BackupError(Exception):
    """The archive, or the request to build/restore it, cannot be honoured safely."""
