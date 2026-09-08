"""The Teams channel as core actually rebuilds it: `_resolved_config` from a
stored `CapaInstallation.config`, straight into the plugin's own `build()`.

The whole point of this file is the seam between core and the plugin, not the
plugin's Teams-shaped internals (those are tested in
`capas/teams_approvals/tests/`). `app_id` and `tenant_id` are not setup-form
fields at all -- they ride along from the `teams_bot` credential type -- so
whether they survive the trip from the setup POST to `build()` is exactly the
kind of thing only an end-to-end assertion catches. It did not survive, once.
"""

from __future__ import annotations

import base64
import sys
import uuid
from pathlib import Path

import pytest

from oc8.channels.registry import _resolved_config
from oc8.secrets.service import store_secret
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

#: teams_approvals's channel package is named `channel`, a name it shares with
#: telegram_approvals and whatsapp_approvals -- and sys.modules is keyed by
#: NAME, not path, so an import here can be handed a sibling's cached copy.
#: Same module-body eviction as test_whatsapp.py/test_telegram.py, and for the
#: same reason: this import runs at COLLECTION time, before any fixture could.
PLUGIN_ROOT = Path(__file__).resolve().parents[3] / "capas" / "teams_approvals"


def _evict_generic_plugin_packages() -> None:
    for _stale in [n for n in sys.modules if n == "channel" or n.startswith("channel.")]:
        del sys.modules[_stale]


_evict_generic_plugin_packages()
sys.path.insert(0, str(PLUGIN_ROOT))

from channel.channel import build  # noqa: E402

_evict_generic_plugin_packages()
sys.path.remove(str(PLUGIN_ROOT))


#: `capas/teams_approvals/plugin.toml`'s `[plugin.config]`, verbatim.
_MANIFEST = {"config": {"secret_ref": "teams/bot_password", "max_classification": "public"}}


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )


async def test_the_installation_config_carries_everything_build_needs(
    app_session: AppSessionFactory,
) -> None:
    """`app_id`/`tenant_id` reach `build()` only through
    `CapaInstallation.config` -- they are `teams_bot` credential-type fields
    that ride along on the one `kind="credential"` setup field, so nothing in
    the manifest holds them. When configure_plugin dropped them instead of
    persisting them, this call raised `ValueError: teams_approvals needs an
    app_id` and the tenant simply had no Teams channel, silently."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await store_secret(db, tenant_id=tenant, name="teams/bot_password", value="app-pw")

        resolved = await _resolved_config(
            db,
            tenant_id=tenant,
            manifest=_MANIFEST,
            installation_config={
                "app_id": "11111111-2222-3333-4444-555555555555",
                "tenant_id": "contoso.onmicrosoft.com",
                "max_classification": "internal",
            },
        )

    assert resolved["bot_token"] == "app-pw"
    assert "secret_ref" not in resolved

    channel = build(resolved)
    assert channel._app_id == "11111111-2222-3333-4444-555555555555"
    assert channel._tenant == "contoso.onmicrosoft.com"
    assert channel._app_password == "app-pw"
    # The wizard's own value beats the manifest's shared default.
    assert channel.capabilities().max_classification == "internal"


async def test_an_installation_config_without_the_ride_along_app_id_builds_nothing(
    app_session: AppSessionFactory,
) -> None:
    """The failure this guards against, stated as a test: a resolved config
    that has the bot password but no `app_id` must refuse to become a channel
    rather than produce one that cannot authenticate anything."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        await store_secret(db, tenant_id=tenant, name="teams/bot_password", value="app-pw")

        resolved = await _resolved_config(
            db, tenant_id=tenant, manifest=_MANIFEST, installation_config={}
        )

    with pytest.raises(ValueError, match="app_id"):
        build(resolved)
