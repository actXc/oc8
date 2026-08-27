# backend/tests/plugins/test_service.py
from __future__ import annotations

import uuid

import pytest

from oc8.capas.service import (
    CoreCompatError,
    DuplicateVersionError,
    install_plugin,
)
from oc8.constants import ACME_TENANT_ID
from tests.conftest import AppSessionFactory


def _mf(**over: object) -> dict[str, object]:
    # Name deliberately distinct from tests/models/test_plugin_models.py's
    # "acme.hello" fixture data — the app_session fixture commits against a
    # session-scoped Postgres container shared by the whole test run, so a
    # collision on (tenant, name) would trip the dup check for the wrong reason.
    base = {"name": "acme.hello-svc", "version": "1.0.0", "type": "core_extension"}
    base.update(over)
    return base


async def test_install_and_dup(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        v = await install_plugin(db, tenant_id=tenant, manifest_data=_mf())
        assert v.semver == "1.0.0"
        with pytest.raises(DuplicateVersionError):
            await install_plugin(db, tenant_id=tenant, manifest_data=_mf())


async def test_core_compat_rejected(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        with pytest.raises(CoreCompatError):
            await install_plugin(
                db, tenant_id=tenant, manifest_data=_mf(name="x.y", core_compat=">=2.0")
            )
