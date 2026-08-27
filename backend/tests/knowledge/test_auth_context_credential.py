from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator

import pytest

from oc8 import config
from oc8.capas.manifest import CredentialTypeSpec, SetupFieldSpec
from oc8.credentials import registry
from oc8.credentials.service import create_credential
from oc8.knowledge.connectors.base import ConnectorError
from oc8.knowledge.connectors.context import SourceAuthContext
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _register_test_type() -> Iterator[None]:
    registry.CORE_CREDENTIAL_TYPES["s3_api"] = CredentialTypeSpec(
        name="s3_api",
        display_name="S3",
        fields=[
            SetupFieldSpec(key="access_key", label="Access key", kind="password", required=False)
        ],
    )
    yield
    registry.CORE_CREDENTIAL_TYPES.pop("s3_api", None)


async def test_credential_resolves_a_field(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        cred = await create_credential(
            db, tenant_id=tenant, name="Prod S3", credential_type="s3_api",
            field_values={"access_key": "AKIA_TEST"},
        )
        # NOTE: no db.commit() here -- app.tenant_id is set LOCAL to the
        # transaction (see app_session fixture), so a mid-test commit would
        # reset it before the credential()/resolve_secret lookup below runs
        # under RLS. flush() already makes the row visible within this same
        # transaction; the fixture commits once for us on context exit.
        auth = SourceAuthContext(db, tenant_id=tenant)
        value = await auth.credential(str(cred.id), "access_key")
        assert value == "AKIA_TEST"


async def test_unknown_credential_id_is_a_connector_error(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        auth = SourceAuthContext(db, tenant_id=tenant)
        with pytest.raises(ConnectorError) as exc_info:
            await auth.credential(str(uuid.uuid4()), "access_key")
        assert "no stored credential with id" in str(exc_info.value)


async def test_a_blank_optional_field_is_a_field_specific_connector_error(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bug 2 regression (Task 15 report, "Misleading error when an optional
    field is left blank"). Before the fix, `SourceAuthContext.credential()`
    caught `CredentialNotFound` unconditionally and always raised
    "no stored credential with id ..." -- indistinguishable from a genuinely
    wrong/deleted credential id -- even when the credential row was
    perfectly real and only ONE optional field (e.g. `s3_api`'s `endpoint`,
    documented "leave empty for AWS") had never been set. The error must now
    name the field and must NOT claim the credential itself is missing."""
    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        cred = await create_credential(
            db,
            tenant_id=tenant,
            name="Prod S3",
            credential_type="s3_api",
            # `access_key` set; nothing else -- mirrors the real s3_api
            # credential type's `endpoint`, left blank as documented.
            field_values={"access_key": "AKIA_TEST"},
        )
        auth = SourceAuthContext(db, tenant_id=tenant)
        with pytest.raises(ConnectorError) as exc_info:
            await auth.credential(str(cred.id), "endpoint")
        message = str(exc_info.value)
        assert "endpoint" in message
        assert "no stored credential" not in message
