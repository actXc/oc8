"""SQLAlchemy `TypeDecorator`s that make column encryption automatic (design
spec §3.1). A model declares one of these exactly like any other column type:

    content: Mapped[str] = mapped_column(EncryptedText("kb_chunk.content"), nullable=False)

The `label` argument is stamped once, at model-definition time, and is bound
into the AEAD associated data (`envelope.aad_for`) together with the current
tenant, so a ciphertext can never be replayed under another tenant or pasted
into another column. `tests/crypto/test_every_content_column_is_classified.py`
asserts no two mapped columns ever share one type *instance* -- sharing an
instance would mean two columns silently claiming the same label.
"""

from __future__ import annotations

import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM, AESGCMSIV
from sqlalchemy import LargeBinary
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator

from oc8.crypto import envelope
from oc8.crypto.context import current_tenant_crypto

_PURPOSE = "content"


def _generation_mismatch(
    label: str, env_generation: int, bound_generation: int
) -> envelope.EnvelopeError:
    return envelope.EnvelopeError(
        f"{label}: envelope generation {env_generation} does not match the bound "
        f"key generation {bound_generation} (DEK rotation is not yet supported "
        "-- design spec §4.2)"
    )


def _purpose_mismatch(label: str, env_purpose: str) -> envelope.EnvelopeError:
    # `env.purpose` comes from the stored bytes, which are not covered by the
    # AAD -- never use it to pick a key. All three types here only ever write
    # `_PURPOSE`, so anything else means the envelope was tampered with, or
    # pasted from another purpose's column, and must fail closed.
    return envelope.EnvelopeError(
        f"{label}: envelope purpose {env_purpose!r} does not match the expected "
        f"purpose {_PURPOSE!r}"
    )


class EncryptedText(TypeDecorator[str]):
    """A `Text` column, encrypted at rest with a random nonce per write.

    Not equality-comparable, not sortable, not usable in a unique constraint
    -- two writes of the same plaintext produce different ciphertext. Use
    `DeterministicText` instead for a column that needs any of those.
    """

    impl = LargeBinary
    cache_ok = True

    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label

    def process_bind_param(self, value: str | None, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        crypto = current_tenant_crypto()
        generation, key = crypto.key_for(_PURPOSE)
        nonce = os.urandom(envelope.NONCE_LEN)
        aad = envelope.aad_for(crypto.tenant_id, self.label)
        ciphertext = AESGCM(key).encrypt(nonce, value.encode("utf-8"), aad)
        return envelope.pack(
            purpose=_PURPOSE, generation=generation, nonce=nonce, ciphertext=ciphertext
        )

    def process_result_value(self, value: bytes | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        crypto = current_tenant_crypto()
        env = envelope.unpack(value)
        if env.purpose != _PURPOSE:
            raise _purpose_mismatch(self.label, env.purpose)
        generation, key = crypto.key_for(_PURPOSE)
        if env.generation != generation:
            raise _generation_mismatch(self.label, env.generation, generation)
        aad = envelope.aad_for(crypto.tenant_id, self.label)
        plaintext = AESGCM(key).decrypt(env.nonce, env.ciphertext, aad)
        return plaintext.decode("utf-8")


class EncryptedJSON(TypeDecorator[Any]):
    """A `JSONB` column holding free-form content, encrypted the same way as
    `EncryptedText`. Serialised with `json.dumps(..., sort_keys=True)` before
    encryption so key order never leaks into the ciphertext length pattern.
    """

    impl = LargeBinary
    cache_ok = True

    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label

    def process_bind_param(self, value: Any | None, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        crypto = current_tenant_crypto()
        generation, key = crypto.key_for(_PURPOSE)
        nonce = os.urandom(envelope.NONCE_LEN)
        aad = envelope.aad_for(crypto.tenant_id, self.label)
        plaintext = json.dumps(value, sort_keys=True).encode("utf-8")
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
        return envelope.pack(
            purpose=_PURPOSE, generation=generation, nonce=nonce, ciphertext=ciphertext
        )

    def process_result_value(self, value: bytes | None, dialect: Dialect) -> Any | None:
        if value is None:
            return None
        crypto = current_tenant_crypto()
        env = envelope.unpack(value)
        if env.purpose != _PURPOSE:
            raise _purpose_mismatch(self.label, env.purpose)
        generation, key = crypto.key_for(_PURPOSE)
        if env.generation != generation:
            raise _generation_mismatch(self.label, env.generation, generation)
        aad = envelope.aad_for(crypto.tenant_id, self.label)
        plaintext = AESGCM(key).decrypt(env.nonce, env.ciphertext, aad)
        result: Any = json.loads(plaintext.decode("utf-8"))
        return result


class DeterministicText(TypeDecorator[str]):
    """A `Text` column that must keep equality lookups, `GROUP BY`, or a
    unique constraint working (design spec §3.1, §6b). Uses AES-GCM-SIV with
    a FIXED nonce: SIV mode is specifically designed to stay safe under nonce
    reuse, which is exactly what makes determinism possible here -- the same
    (key, label, plaintext) always encrypts to the same ciphertext.
    """

    impl = LargeBinary
    cache_ok = True

    _FIXED_NONCE = b"\x00" * envelope.NONCE_LEN

    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label

    def process_bind_param(self, value: str | None, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        crypto = current_tenant_crypto()
        generation, key = crypto.key_for(_PURPOSE)
        aad = envelope.aad_for(crypto.tenant_id, self.label)
        ciphertext = AESGCMSIV(key).encrypt(self._FIXED_NONCE, value.encode("utf-8"), aad)
        return envelope.pack(
            purpose=_PURPOSE,
            generation=generation,
            nonce=self._FIXED_NONCE,
            ciphertext=ciphertext,
        )

    def process_result_value(self, value: bytes | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        crypto = current_tenant_crypto()
        env = envelope.unpack(value)
        if env.purpose != _PURPOSE:
            raise _purpose_mismatch(self.label, env.purpose)
        generation, key = crypto.key_for(_PURPOSE)
        if env.generation != generation:
            raise _generation_mismatch(self.label, env.generation, generation)
        aad = envelope.aad_for(crypto.tenant_id, self.label)
        plaintext = AESGCMSIV(key).decrypt(env.nonce, env.ciphertext, aad)
        return plaintext.decode("utf-8")
