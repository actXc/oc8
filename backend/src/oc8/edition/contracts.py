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

    def service_routers(self) -> Sequence[APIRouter]:
        """Return service-to-service API routers contributed by this edition.

        Unlike routers(), these are mounted WITHOUT get_principal/deny_*
        dependencies -- each router verifies its own service-to-service
        credential (e.g. a static bearer token), the same way
        internal_agent_router verifies its own run-scoped agent token.

        Defaults to no routers so existing extensions (e.g.
        SupervisionExtension) that only contribute operator routers don't
        need to implement it. `Protocol` subclasses inherit this concrete
        body through ordinary nominal inheritance.
        """
        return ()
