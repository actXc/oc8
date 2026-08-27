"""The credential-vault envelope for an export (design doc §4). Bound to a
passphrase the OPERATOR controls, distinct from the tenant DEK that protects
secrets at rest -- the whole reason an export is portable off this instance.

Secret values are encrypted with the tenant's DEK, which is itself wrapped by
the INSTANCE's `OC8_SECRET_KEK`. That makes the stored ciphertext portable
nowhere: restoring it onto another instance yields garbage. This module is
the separate path that re-encrypts under a key the operator controls instead
-- a passphrase, run through scrypt to derive an AES-256-GCM key.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from collections.abc import Mapping
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from oc8.backup.errors import BackupError

_N, _R, _P = 16384, 8, 1
_SALT_LEN = 32


class WrongPassphrase(BackupError):
    """The passphrase does not decrypt this archive's secrets.json."""


def _derive_key(passphrase: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=n, r=r, p=p, dklen=32)


def _aad(meta: Mapping[str, Any]) -> bytes:
    """Bind the manifest's KDF parameters to the ciphertext.

    `salt`, `n`, `r` and `p` live in `manifest.json`, OUTSIDE the envelope --
    so without this they are unauthenticated attacker-editable input. Changing
    them already fails the GCM tag (a different salt derives a different key),
    which means this is defence in depth rather than a fix for a live hole.
    But it costs one line and it makes the failure structural instead of
    incidental: the tag now covers the parameters themselves, so no future
    KDF change can accidentally create a variant where tampering succeeds.

    Sorted keys because the AAD must be byte-identical on both sides.
    """
    return json.dumps(meta, sort_keys=True, separators=(",", ":")).encode()


def encrypt_secrets(secrets: dict[str, str], passphrase: str) -> tuple[bytes, dict[str, Any]]:
    """Encrypt the `{name: value}` map under a passphrase-derived key.

    Returns `(secrets_json_bytes, manifest_secrets_meta)`: the bytes to write
    as `secrets.json`, and the metadata that goes verbatim into
    `manifest.secrets` (design doc §3's example) -- never null when this
    function is called, so a caller that always assigns this into the
    manifest can never end up with `secrets: {}` while `secrets.json` sits
    unread in the tar (Task 4's reader keys off `is not None`, not
    truthiness).
    """
    salt = os.urandom(_SALT_LEN)
    key = _derive_key(passphrase, salt, n=_N, r=_R, p=_P)
    nonce = os.urandom(12)
    meta = {
        "count": len(secrets),
        "kdf": "scrypt",
        "n": _N,
        "r": _R,
        "p": _P,
        "salt": base64.b64encode(salt).decode("ascii"),
    }
    ciphertext = AESGCM(key).encrypt(nonce, json.dumps(secrets).encode(), _aad(meta))
    envelope = {
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    return json.dumps(envelope).encode(), meta


def decrypt_secrets(secrets_json: bytes, passphrase: str, meta: dict[str, Any]) -> dict[str, str]:
    """Decrypt `secrets.json` back into the `{name: value}` map.

    Raises `WrongPassphrase` -- and nothing else, for a bad passphrase or a
    corrupted envelope -- before returning anything, so a caller that only
    starts writing after this returns can never write a partial result.
    AES-GCM's authentication tag is what makes "wrong key" a hard failure
    here instead of silently returning garbage plaintext.
    """
    # A corrupt envelope and a wrong passphrase are the same event to the
    # operator -- "this file plus this passphrase does not open" -- and both
    # must leave the caller having written nothing. Letting a JSONDecodeError
    # or a base64 error escape instead would surface as a 500 on a path whose
    # whole contract is a clean refusal.
    try:
        envelope = json.loads(secrets_json)
        salt = base64.b64decode(meta["salt"])
        nonce = base64.b64decode(envelope["nonce"])
        ciphertext = base64.b64decode(envelope["ciphertext"])
        key = _derive_key(passphrase, salt, n=meta["n"], r=meta["r"], p=meta["p"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise WrongPassphrase("wrong passphrase, or the archive is corrupt") from exc
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _aad(meta))
    except InvalidTag as exc:
        raise WrongPassphrase("wrong passphrase, or the archive is corrupt") from exc
    result: dict[str, str] = json.loads(plaintext)
    return result
