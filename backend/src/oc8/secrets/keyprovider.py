"""Envelope-encryption key provider abstraction.

A `KeyProvider` wraps/unwraps per-tenant data-encryption-keys (DEKs) with a
root key-encryption-key (KEK). The env-backed implementation here reads the
KEK from `Settings.secret_kek` (base64-encoded, 256-bit). It fails closed:
a missing or malformed KEK raises `SecretStoreUnavailable` rather than ever
falling back to storing secrets in plaintext.
"""

from __future__ import annotations

import base64
import binascii
import os
from typing import Protocol, runtime_checkable

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from oc8.config import get_settings

_NONCE_LEN = 12


class SecretError(Exception):
    """Base class for secret-store errors."""


class SecretStoreUnavailable(SecretError):
    """Raised when the secret store cannot be used (e.g. no valid KEK configured)."""


@runtime_checkable
class KeyProvider(Protocol):
    """Wraps/unwraps per-tenant DEKs with a root KEK."""

    key_version: str

    def wrap_dek(self, dek: bytes) -> bytes: ...

    def unwrap_dek(self, wrapped: bytes) -> bytes: ...


class EnvKeyProvider:
    """KeyProvider backed by a KEK read from an environment-derived setting.

    The KEK must be a base64-encoded 256-bit (32-byte) value. Anything else
    (empty string, invalid base64, wrong decoded length) raises
    `SecretStoreUnavailable` at construction time — fail closed, never a
    plaintext fallback.
    """

    key_version = "env:v1"

    def __init__(self, kek_b64: str) -> None:
        self._kek = validate_kek(kek_b64)

    def wrap_dek(self, dek: bytes) -> bytes:
        nonce = os.urandom(_NONCE_LEN)
        ct = AESGCM(self._kek).encrypt(nonce, dek, self.key_version.encode())
        return nonce + ct

    def unwrap_dek(self, wrapped: bytes) -> bytes:
        nonce, ct = wrapped[:_NONCE_LEN], wrapped[_NONCE_LEN:]
        result: bytes = AESGCM(self._kek).decrypt(nonce, ct, self.key_version.encode())
        return result


def get_key_provider() -> KeyProvider:
    """Build the configured KeyProvider. Fails closed if no valid KEK is set.

    `secret_kek` is read defensively: if the setting is absent entirely, the
    store is simply unavailable (the documented fail-closed behaviour) rather
    than raising an opaque AttributeError.
    """
    return EnvKeyProvider(getattr(get_settings(), "secret_kek", ""))


_AUDIT_MAC_INFO = b"oc8-audit-chain-v1"


def validate_kek(kek_b64: str) -> bytes:
    """Decode and validate the root KEK, or raise SecretStoreUnavailable.

    Shared by EnvKeyProvider and audit_mac_key so the two cannot drift on what
    counts as a valid KEK.
    """
    if not kek_b64:
        raise SecretStoreUnavailable("secret_kek is not configured")
    try:
        kek = base64.b64decode(kek_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SecretStoreUnavailable("secret_kek is not valid base64") from exc
    if len(kek) != 32:
        raise SecretStoreUnavailable("secret_kek must decode to exactly 32 bytes (256-bit)")
    return kek


def audit_mac_key() -> bytes:
    """The audit chain's MAC key, HKDF-derived from the root KEK (§12.5).

    A dedicated info label keeps this key useless for unwrapping DEKs: the two
    purposes share a root, not a key. No salt -- the KEK is already a uniformly
    random 256-bit value, which is exactly the case where HKDF's extract step
    buys nothing.
    """
    kek = validate_kek(get_settings().secret_kek)
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_AUDIT_MAC_INFO).derive(kek)
