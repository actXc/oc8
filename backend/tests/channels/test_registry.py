"""channels/registry.py resolves one tenant's usable approval channels: a
plugin's static [plugin.config], its secret reference turned into a value, and
-- since the setup wizard learned to collect a value that varies per tenant
(§13, PluginInstallation.config) -- whichever of those the tenant actually
submitted, winning over the manifest's shared default.
"""

from __future__ import annotations

import base64
import uuid

import pytest

from oc8.channels.registry import _resolved_config
from oc8.secrets.service import store_secret
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )


async def test_a_tenant_supplied_value_wins_over_the_manifest_default(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    manifest = {
        "config": {
            "secret_ref": "whatsapp/access_token",
            "max_classification": "public",
            "phone_number_id": "",
        }
    }
    async with app_session(tenant) as db:
        # No commit here: app_session commits on its own exit, and committing
        # mid-block unbinds the RLS GUC (see [[tenant-guc-dies-at-commit]] --
        # the flush below is already visible to a SELECT in the same session).
        await store_secret(db, tenant_id=tenant, name="whatsapp/access_token", value="tok-123")

        resolved = await _resolved_config(
            db,
            tenant_id=tenant,
            manifest=manifest,
            installation_config={"phone_number_id": "48123456789"},
        )

    assert resolved["bot_token"] == "tok-123"
    assert resolved["phone_number_id"] == "48123456789"
    assert resolved["max_classification"] == "public"
    assert "secret_ref" not in resolved, "the reference itself must never leave this module"


async def test_no_installation_override_falls_back_to_the_manifest_default(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    manifest = {"config": {"phone_number_id": "", "max_classification": "public"}}
    async with app_session(tenant) as db:
        resolved = await _resolved_config(
            db, tenant_id=tenant, manifest=manifest, installation_config={}
        )
    assert resolved["phone_number_id"] == ""
