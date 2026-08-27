"""The one run-scoped-JWT minting call every CLI-harness plugin needs,
identical to nanoclaw_runtime/runtime/runtime.py's own (runtime.py:466-472) --
kind="agent" so the internal API's _run_for_token check accepts it, scoped
to exactly this run so a leaked token from one run cannot touch another."""

from __future__ import annotations

import uuid

from oc8 import models as m
from oc8.auth import get_identity_provider


def run_token(*, tenant_id: uuid.UUID, agent: m.Agent, run_id: uuid.UUID) -> str:
    return get_identity_provider().mint(
        tenant_id=tenant_id,
        subject=f"agent:{agent.id}",
        role="agent_default",
        kind="agent",
        scopes=[f"run:{run_id}"],
    )
