"""Pure TOTP (RFC 6238) logic for standalone 2FA: secret generation, the
otpauth:// provisioning URI, code verification, and one-time backup codes.

No DB access, no FastAPI -- mirrors auth/password.py's shape exactly. The
caller (api/v1/totp.py) owns persistence; this module only knows how to
generate and check.

Backup codes are hashed with the SAME Argon2id primitive as the password
itself (auth/password.py) -- one-way, never needs decrypting back to
plaintext.
"""

from __future__ import annotations

import secrets

import pyotp

from oc8.auth.password import hash_password, verify_password

#: RFC 6238's standard tolerance for clock drift: the code for the current
#: step, the one before it, and the one after it are all accepted.
_VALID_WINDOW = 1

#: 10 backup codes, each a short random alphanumeric string -- readable and
#: typeable, not a UUID.
_BACKUP_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
_BACKUP_CODE_LENGTH = 10


def generate_secret() -> str:
    """A fresh base32 TOTP shared secret."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, *, account_name: str, issuer: str = "oc8") -> str:
    """The otpauth://totp/... URI an authenticator app's QR scanner expects."""
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=issuer)


def verify_code(secret: str, code: str) -> bool:
    """Whether `code` is valid for `secret` within the +/-1 step (30s) window."""
    try:
        return pyotp.TOTP(secret).verify(code, valid_window=_VALID_WINDOW)
    except Exception:
        # A malformed code (wrong length, non-digit) must fail closed, not
        # raise out of a request handler -- same doctrine as
        # auth/password.py's verify_password.
        return False


def generate_backup_codes(count: int = 10) -> list[str]:
    """`count` unique, single-use recovery codes, shown to the caller exactly
    once by whoever calls this (never persisted in plaintext)."""
    return [
        "".join(secrets.choice(_BACKUP_CODE_ALPHABET) for _ in range(_BACKUP_CODE_LENGTH))
        for _ in range(count)
    ]


def hash_backup_code(code: str) -> str:
    """Argon2id-hash one backup code for storage -- same primitive as a
    password, since a backup code IS a short-lived credential."""
    return hash_password(code)


def verify_backup_code(code: str, stored_hash: str) -> bool:
    return verify_password(code, stored_hash)
