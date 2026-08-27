from __future__ import annotations

import uuid

import pytest

from oc8.crypto.context import (
    CryptoContextMissing,
    TenantCrypto,
    bound_tenant_crypto,
    current_tenant_crypto,
)


def test_current_tenant_crypto_raises_when_unbound() -> None:
    with pytest.raises(CryptoContextMissing, match="no tenant crypto context is bound"):
        current_tenant_crypto()


def test_bound_tenant_crypto_binds_for_the_block_only() -> None:
    crypto = TenantCrypto(tenant_id=uuid.uuid4(), keys={"content": (1, b"\x00" * 32)})
    with bound_tenant_crypto(crypto):
        assert current_tenant_crypto() is crypto
    with pytest.raises(CryptoContextMissing):
        current_tenant_crypto()


def test_bound_tenant_crypto_restores_on_exception() -> None:
    crypto = TenantCrypto(tenant_id=uuid.uuid4(), keys={})
    with pytest.raises(ValueError):
        with bound_tenant_crypto(crypto):
            raise ValueError("boom")
    with pytest.raises(CryptoContextMissing):
        current_tenant_crypto()


def test_bound_tenant_crypto_nests_and_restores_outer_value() -> None:
    tenant_a = TenantCrypto(tenant_id=uuid.uuid4(), keys={})
    tenant_b = TenantCrypto(tenant_id=uuid.uuid4(), keys={})
    with bound_tenant_crypto(tenant_a):
        with bound_tenant_crypto(tenant_b):
            assert current_tenant_crypto() is tenant_b
        assert current_tenant_crypto() is tenant_a


def test_key_for_returns_generation_and_bytes() -> None:
    crypto = TenantCrypto(tenant_id=uuid.uuid4(), keys={"content": (3, b"\x01" * 32)})
    generation, key = crypto.key_for("content")
    assert generation == 3
    assert key == b"\x01" * 32


def test_key_for_raises_for_unbound_purpose() -> None:
    crypto = TenantCrypto(tenant_id=uuid.uuid4(), keys={"content": (1, b"\x00" * 32)})
    with pytest.raises(CryptoContextMissing, match="no 'evidence' key bound"):
        crypto.key_for("evidence")
