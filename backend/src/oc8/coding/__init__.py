"""Coding-loop capability: sandbox-backed agentic coding tools."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from oc8.coding.tools import CODING_TOOLS, Toolset
from oc8.coding.toolset import SandboxToolset
from oc8.sandbox import SandboxDriver, SandboxSpec, sandbox_session

__all__ = ["CODING_TOOLS", "SandboxToolset", "Toolset", "coding_session"]


@asynccontextmanager
async def coding_session(
    spec: SandboxSpec, *, driver: SandboxDriver | None = None
) -> AsyncIterator[SandboxToolset]:
    async with sandbox_session(spec, driver=driver) as (drv, handle):
        yield SandboxToolset(drv, handle)
