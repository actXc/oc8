"""Authentication: principals, identity providers, and password utilities."""

from oc8.auth.principal import Principal
from oc8.auth.provider import (
    DevIdentityProvider,
    IdentityProvider,
    get_identity_provider,
)
from oc8.auth.password import (
    hash_password,
    verify_password,
    PasswordHashingError,
    InvalidPasswordHash,
    PasswordVerificationFailed,
)

__all__ = [
    "DevIdentityProvider",
    "IdentityProvider",
    "InvalidPasswordHash",
    "PasswordHashingError",
    "PasswordVerificationFailed",
    "Principal",
    "get_identity_provider",
    "hash_password",
    "verify_password",
]
