from __future__ import annotations

import base64
import os

import pytest
from cryptography.exceptions import InvalidTag

from oc8.secrets.keyprovider import (
    EnvKeyProvider,
    SecretStoreUnavailable,
    get_key_provider,
)

VALID_KEK_B64 = base64.b64encode(b"\x00" * 32).decode()


def test_wrap_unwrap_roundtrip() -> None:
    provider = EnvKeyProvider(VALID_KEK_B64)
    dek = os.urandom(32)

    wrapped = provider.wrap_dek(dek)

    assert wrapped != dek
    assert provider.unwrap_dek(wrapped) == dek


def test_wrapped_output_is_nonce_plus_ciphertext() -> None:
    provider = EnvKeyProvider(VALID_KEK_B64)
    dek = os.urandom(32)

    wrapped = provider.wrap_dek(dek)

    # 12-byte nonce + ciphertext + 16-byte GCM tag.
    assert len(wrapped) == 12 + len(dek) + 16


def test_key_version_is_env_v1() -> None:
    provider = EnvKeyProvider(VALID_KEK_B64)
    assert provider.key_version == "env:v1"


def test_empty_kek_raises_secret_store_unavailable() -> None:
    with pytest.raises(SecretStoreUnavailable):
        EnvKeyProvider("")


def test_invalid_base64_kek_raises_secret_store_unavailable() -> None:
    with pytest.raises(SecretStoreUnavailable):
        EnvKeyProvider("not-valid-base64!!!")


def test_wrong_length_kek_raises_secret_store_unavailable() -> None:
    short_kek = base64.b64encode(b"\x00" * 16).decode()
    with pytest.raises(SecretStoreUnavailable):
        EnvKeyProvider(short_kek)


def test_unwrap_with_wrong_kek_raises_invalid_tag() -> None:
    provider = EnvKeyProvider(VALID_KEK_B64)
    dek = os.urandom(32)
    wrapped = provider.wrap_dek(dek)

    other_kek_b64 = base64.b64encode(b"\x01" * 32).decode()
    other_provider = EnvKeyProvider(other_kek_b64)

    with pytest.raises(InvalidTag):
        other_provider.unwrap_dek(wrapped)


def test_unwrap_tampered_ciphertext_raises_invalid_tag() -> None:
    provider = EnvKeyProvider(VALID_KEK_B64)
    dek = os.urandom(32)
    wrapped = bytearray(provider.wrap_dek(dek))
    wrapped[-1] ^= 0xFF  # flip a byte in the GCM tag

    with pytest.raises(InvalidTag):
        provider.unwrap_dek(bytes(wrapped))


def test_get_key_provider_reads_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from oc8 import config

    config.get_settings.cache_clear()
    monkeypatch.setenv("OC8_SECRET_KEK", VALID_KEK_B64)

    provider = get_key_provider()

    assert isinstance(provider, EnvKeyProvider)
    assert provider.key_version == "env:v1"

    config.get_settings.cache_clear()


def test_get_key_provider_fails_closed_without_kek(monkeypatch: pytest.MonkeyPatch) -> None:
    from oc8 import config

    config.get_settings.cache_clear()
    monkeypatch.setenv("OC8_SECRET_KEK", "")

    with pytest.raises(SecretStoreUnavailable):
        get_key_provider()

    config.get_settings.cache_clear()
