from __future__ import annotations

import uuid

from oc8.auth import get_identity_provider


def test_mint_and_verify_scopes() -> None:
    prov = get_identity_provider()
    tok = prov.mint(
        tenant_id=uuid.uuid4(),
        subject="plugin:p1",
        role="plugin",
        kind="plugin",
        scopes=["api:tasks.read"],
    )
    principal = prov.verify(tok)
    assert principal.kind == "plugin"
    assert principal.scopes == ["api:tasks.read"]


def test_verify_defaults_scopes_to_empty_list() -> None:
    prov = get_identity_provider()
    tok = prov.mint(tenant_id=uuid.uuid4(), subject="u", role="org_admin")
    principal = prov.verify(tok)
    assert principal.scopes == []
