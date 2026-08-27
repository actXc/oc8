"""Narrow, explicit contracts for build-time edition composition."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from fastapi import APIRouter


class EditionExtension(Protocol):
    """A trusted edition contribution mounted by the Community app factory.

    This is deliberately limited to routers until an implemented edition needs
    another composition surface.  It is not the Marketplace plugin contract.
    """

    def routers(self) -> Sequence[APIRouter]:
        """Return operator API routers contributed by this edition."""
        ...
