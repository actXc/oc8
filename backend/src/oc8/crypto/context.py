"""Tenant-bound crypto context (design spec §3.2).

`tenant_session()` will bind this immediately after setting the
`app.tenant_id` RLS GUC -- wired in a later rollout step (design spec §9 step
2), once a `tenant_dek.purpose`/`.generation` migration exists to fetch real
keys from. Until then nothing in application code calls this module; it
exists standalone so `oc8/crypto/types.py` has something real to call, and so
both are fully testable before anything depends on them.

Fail-closed, matching `oc8.secrets.keyprovider`'s `SecretStoreUnavailable`: no
context bound means `CryptoContextMissing`, never a plaintext fallback.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


class CryptoContextMissing(Exception):
    """Encrypted-column code ran with no tenant crypto context bound, or asked
    for a purpose/generation that isn't."""


@dataclass(frozen=True)
class TenantCrypto:
    """One tenant's unwrapped keys, by purpose ('secrets' | 'content' |
    'evidence'). Each value is `(generation, raw_dek_bytes)` -- only the
    current generation is carried; decrypting an envelope written under an
    older generation is out of scope until DEK rotation lands (design spec
    §4.2), so `key_for` raising on a mismatch is the correct fail-closed
    behaviour for now, not a bug.
    """

    tenant_id: uuid.UUID
    keys: Mapping[str, tuple[int, bytes]] = field(repr=False)

    def key_for(self, purpose: str) -> tuple[int, bytes]:
        try:
            return self.keys[purpose]
        except KeyError:
            raise CryptoContextMissing(
                f"no {purpose!r} key bound for tenant {self.tenant_id}"
            ) from None


_current: ContextVar[TenantCrypto | None] = ContextVar("_current_tenant_crypto", default=None)


@contextmanager
def bound_tenant_crypto(crypto: TenantCrypto | None) -> Iterator[None]:
    """Bind `crypto` as the current tenant's keys for the duration of the
    block, restoring whatever was bound before on exit (including on an
    exception)."""
    token = _current.set(crypto)
    try:
        yield
    finally:
        _current.reset(token)


def current_tenant_crypto() -> TenantCrypto:
    """The bound `TenantCrypto`, or raise `CryptoContextMissing`."""
    crypto = _current.get()
    if crypto is None:
        raise CryptoContextMissing("no tenant crypto context is bound in this task/request")
    return crypto
