"""`channels_for_tenant` must load a plugin's code itself before reading its
contributed channels -- it did not, which worked by accident in the
`backend`/`worker` processes (something else always happened to load an
enabled approval-channel plugin first) and silently returned nothing in a
process where nothing else ever does, e.g. `scheduler` the moment
`oc8.channels.poll` became its first caller.

`oc8.capas.contributions.reset_for_tests()` simulates exactly that: a
process where this plugin's `register()` entry point has never run.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from oc8.capas.contributions import reset_for_tests
from oc8.capas.discovery import find_plugin
from oc8.capas.lifecycle import enable_plugin
from oc8.capas.loader import reset_for_tests as loader_reset_for_tests
from oc8.capas.service import install_plugin
from oc8.channels.registry import channels_for_tenant
from oc8.secrets.service import store_secret
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

_REPO_PLUGINS_DIR = Path(__file__).resolve().parents[3] / "capas"


@pytest.fixture(autouse=True)
def _real_plugins_path(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from oc8.config import get_settings

    monkeypatch.setenv("OC8_CAPAS_PATH", str(_REPO_PLUGINS_DIR))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch, _real_plugins_path: None) -> Iterator[None]:
    # Same fixture as test_telegram.py's, for the same reason: whichever of
    # the two files' plugins-path/KEK fixtures runs second would otherwise
    # decide whether the app process sees a KEK at all.
    from oc8.config import get_settings

    monkeypatch.setenv("OC8_SECRET_KEK", base64.b64encode(bytes(range(32))).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_a_process_that_never_loaded_the_plugin_still_discovers_its_channel(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    discovered = find_plugin("telegram_approvals")
    assert discovered is not None and discovered.valid and discovered.manifest is not None

    async with app_session(tenant) as db:
        version = await install_plugin(db, tenant_id=tenant, manifest_data=discovered.manifest)
        await enable_plugin(
            db,
            tenant_id=tenant,
            capa_id=version.capa_id,
            granted_permissions=list(version.permissions),
        )
        # A channel factory refuses to build with no token (`build({})`
        # raises ValueError -- see test_telegram.py's own coverage of that),
        # so a bare install+enable with no credential would still be
        # rejected by channels_for_tenant's own try/except around the
        # factory call and never prove this fix either way.
        await store_secret(
            db, tenant_id=tenant, name="telegram/bot_token", value="123456:ABC-fake-token"
        )
        # No commit here: a commit ends the transaction that
        # `app.tenant_id` was SET LOCAL onto (this session's RLS binding
        # is transaction-local), and the very next query below -- reading
        # the Capa/CapaInstallation rows just written -- would then see
        # nothing at all for this tenant. Same failure mode `oc8.mail.send`
        # and Task 4's email-change endpoint were both bitten by earlier
        # this session; `app_session`'s own context manager commits once,
        # on exit.

        # Simulate a fresh process: nothing has called this plugin's
        # register() entry point in-process yet, exactly the scheduler's
        # starting state before this fix. Both caches, not just one --
        # `capas.loader.load_plugin` memoizes its own True/False outcome
        # per plugin_id (`_outcomes`) independently of whether the
        # contributions catalogue (`_catalogue`, reset by
        # `contributions.reset_for_tests()` alone) still holds what that
        # earlier load registered. A real fresh process starts with BOTH
        # empty; a test sharing a process with test_telegram.py's own
        # already-succeeded load would otherwise hit `load_plugin`'s cache
        # and skip register() a second time, silently leaving the
        # catalogue this test just cleared unpopulated -- a false failure
        # in the test, not a bug in channels_for_tenant.
        loader_reset_for_tests()
        reset_for_tests()

        channels = await channels_for_tenant(db, tenant_id=tenant)

    assert "telegram" in channels, (
        "channels_for_tenant must load the plugin itself, not assume some "
        "earlier, unrelated code path already did"
    )
