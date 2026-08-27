from __future__ import annotations

import base64
import uuid

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from oc8 import models as m
from oc8.secrets.service import (
    SecretNotFound,
    _aad,
    delete_secret,
    get_or_create_dek,
    resolve_secret,
    store_secret,
)
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    # get_key_provider reads settings.secret_kek via get_settings(); set it.
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )


async def test_store_resolve_roundtrip(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        await store_secret(s, tenant_id=tenant, name="gh", value="ghp_secret", kind="api_key")
        assert await resolve_secret(s, tenant_id=tenant, ref="gh") == "ghp_secret"


async def test_tamper_fails(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        sec = await store_secret(s, tenant_id=tenant, name="gh", value="x")
        sec.ciphertext = sec.ciphertext[:-1] + bytes([sec.ciphertext[-1] ^ 0xFF])
        await s.flush()
        with pytest.raises(InvalidTag):
            await resolve_secret(s, tenant_id=tenant, ref="gh")


async def test_per_tenant_dek_isolation(app_session: AppSessionFactory) -> None:
    """Two tenants get genuinely distinct DEKs, and one cannot decrypt the other's row.

    This must fail if `get_or_create_dek` ever regressed to a shared/global DEK —
    so it compares the unwrapped key bytes and attempts a real cross-tenant
    decrypt, rather than only counting RLS-scoped rows.
    """
    ta, tb = uuid.uuid4(), uuid.uuid4()

    async with app_session(ta) as s:
        await store_secret(s, tenant_id=ta, name="k", value="A")
        dek_a = await get_or_create_dek(s, tenant_id=ta)
        row_a = (
            await s.execute(select(m.Secret).where(m.Secret.name == "k"))
        ).scalar_one()
        nonce_a, ct_a = row_a.nonce, row_a.ciphertext

    async with app_session(tb) as s:
        await store_secret(s, tenant_id=tb, name="k", value="B")
        assert await resolve_secret(s, tenant_id=tb, ref="k") == "B"
        dek_b = await get_or_create_dek(s, tenant_id=tb)

    # 1. The per-tenant DEKs are genuinely different key material.
    assert dek_a != dek_b

    # 2. Tenant B's DEK cannot decrypt tenant A's ciphertext (wrong key).
    with pytest.raises(InvalidTag):
        AESGCM(dek_b).decrypt(nonce_a, ct_a, _aad(ta, "k"))

    # 3. Even with A's own DEK, the AAD binds the row to (tenant, name):
    #    replaying it under tenant B's identity fails.
    with pytest.raises(InvalidTag):
        AESGCM(dek_a).decrypt(nonce_a, ct_a, _aad(tb, "k"))

    # 4. Sanity: A's own key + own AAD still decrypts to A's value.
    assert AESGCM(dek_a).decrypt(nonce_a, ct_a, _aad(ta, "k")).decode() == "A"


async def test_missing_ref_raises(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        with pytest.raises(SecretNotFound):
            await resolve_secret(s, tenant_id=tenant, ref="nope")


async def test_delete(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        await store_secret(s, tenant_id=tenant, name="k", value="v")
        await delete_secret(s, tenant_id=tenant, ref="k")
        with pytest.raises(SecretNotFound):
            await resolve_secret(s, tenant_id=tenant, ref="k")
