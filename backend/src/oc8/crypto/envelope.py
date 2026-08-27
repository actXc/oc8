"""Ciphertext envelope format for application-level content encryption
(design spec §3.4):

    version(1) | purpose(1) | generation(2, BE) | nonce(12) | AESGCM(plaintext) || tag

Stored as `bytea`, not base64-in-text, to avoid the ~33% base64 overhead on
every encrypted column.
"""

from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass

VERSION = 1
# A later rollout step (design spec §8) needs compress-then-encrypt for some
# columns. No compression code exists yet, and none is added here -- but when
# it lands, "this payload is compressed" is signalled by bumping VERSION to a
# new value, not by adding a field to the format above. The version byte
# exists precisely to allow that kind of extension without altering the
# layout for existing, already-written envelopes.
NONCE_LEN = 12

_PURPOSE_CODES: dict[str, int] = {"secrets": 1, "content": 2, "evidence": 3}
_PURPOSE_NAMES: dict[int, str] = {code: name for name, code in _PURPOSE_CODES.items()}
_HEADER = struct.Struct(">BBH")  # version, purpose code, generation

# Domain-separates this module's AAD from the pre-existing Secret Store's
# (`oc8.secrets.service._aad`), which independently builds `f"{tenant_id}|{name}"`
# -- same shape, different domain. Without this prefix the two are only kept
# apart by using different DEKs; the prefix makes that explicit rather than
# incidental. Never persisted -- AAD is used at encrypt/decrypt time only.
_AAD_DOMAIN_PREFIX = b"oc8:col:v1|"


class EnvelopeError(Exception):
    """Malformed envelope, or a header that doesn't match what the caller
    expected (unknown version, unknown purpose code)."""


@dataclass(frozen=True)
class Envelope:
    purpose: str
    generation: int
    nonce: bytes
    ciphertext: bytes


def aad_for(tenant_id: uuid.UUID, label: str) -> bytes:
    """AEAD associated data binding a ciphertext to one tenant and one
    `table.column` label, so it cannot be replayed under another tenant or
    pasted into another column (design spec §3.4)."""
    return _AAD_DOMAIN_PREFIX + f"{tenant_id}|{label}".encode()


def pack(*, purpose: str, generation: int, nonce: bytes, ciphertext: bytes) -> bytes:
    """Build the on-disk envelope. `ciphertext` already carries the GCM tag."""
    if purpose not in _PURPOSE_CODES:
        raise EnvelopeError(f"unknown purpose {purpose!r}")
    if not 0 <= generation <= 0xFFFF:
        raise EnvelopeError(f"generation {generation} out of range for a 16-bit field")
    if len(nonce) != NONCE_LEN:
        raise EnvelopeError(f"nonce must be {NONCE_LEN} bytes, got {len(nonce)}")
    header = _HEADER.pack(VERSION, _PURPOSE_CODES[purpose], generation)
    return header + nonce + ciphertext


def unpack(data: bytes) -> Envelope:
    """Parse an on-disk envelope. Raises `EnvelopeError` on any malformed
    input -- never returns a partially-parsed result."""
    if len(data) < _HEADER.size + NONCE_LEN:
        raise EnvelopeError(f"envelope too short: {len(data)} bytes")
    version, purpose_code, generation = _HEADER.unpack_from(data, 0)
    if version != VERSION:
        raise EnvelopeError(f"unsupported envelope version {version}")
    if purpose_code not in _PURPOSE_NAMES:
        raise EnvelopeError(f"unknown purpose code {purpose_code}")
    offset = _HEADER.size
    nonce = data[offset : offset + NONCE_LEN]
    ciphertext = data[offset + NONCE_LEN :]
    return Envelope(
        purpose=_PURPOSE_NAMES[purpose_code],
        generation=generation,
        nonce=nonce,
        ciphertext=ciphertext,
    )
