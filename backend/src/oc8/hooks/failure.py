"""on_failure adapter: durably records a hook failure in a fresh session so a
poisoned request transaction cannot swallow the circuit-breaker increment."""

from __future__ import annotations

import uuid

from oc8.capas.lifecycle import record_failure
from oc8.db.session import tenant_session
from oc8.hooks.bus import OnFailure


def make_on_failure(tenant_id: uuid.UUID, plugin_lookup: dict[str, uuid.UUID]) -> OnFailure:
    """``plugin_lookup`` maps the string ``HookHandler.plugin_id`` (as stored
    on the handler) to the plugin's UUID primary key, since dispatch only
    carries the string id."""

    async def _on_failure(plugin_id: str) -> None:
        pid = plugin_lookup.get(plugin_id)
        if pid is None:
            return
        async with tenant_session(tenant_id) as db:
            await record_failure(db, tenant_id=tenant_id, capa_id=pid)

    return _on_failure
