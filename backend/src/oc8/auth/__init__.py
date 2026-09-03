"""Authentication: principals, identity providers, and password utilities."""

from oc8.auth.password import (
    InvalidPasswordHash,
    PasswordHashingError,
    PasswordVerificationFailed,
    hash_password,
    verify_password,
)
from oc8.auth.principal import Principal
from oc8.auth.provider import (
    DevIdentityProvider,
    IdentityProvider,
    get_identity_provider,
    set_identity_provider,
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
    "set_identity_provider",
    "verify_password",
]
