"""Envelope-encryption secret store service (tech-spec §12.3).

Ties `KeyProvider` (per-tenant DEK wrap/unwrap with a root KEK) to the
`Secret`/`TenantDek` RLS tables: each tenant gets a lazily-created DEK
(wrapped by the KEK and stored in `tenant_dek`); each secret value is
encrypted with that tenant's DEK via AES-256-GCM, with the ciphertext bound
to `tenant_id|name` as AEAD associated data so a row can't be replayed under
another tenant or name.

This module only `add`s and `flush`es — it never commits. The caller (the
API endpoint) owns the transaction boundary; RLS tenant isolation is a
transaction-local Postgres GUC, so a mid-service commit here would break
isolation for any writes that happen later in the same request.
"""

from __future__ import annotations

import os
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.secrets.keyprovider import SecretError, get_key_provider


class SecretNotFound(SecretError):
    """No secret with the given ref for this tenant."""


def _aad(tenant_id: uuid.UUID, name: str) -> bytes:
    return f"{tenant_id}|{name}".encode()


async def get_or_create_dek(db: AsyncSession, *, tenant_id: uuid.UUID) -> bytes:
    """Return the tenant's data-encryption-key, creating and wrapping one if absent."""
    kp = get_key_provider()
    row = (
        await db.execute(select(m.TenantDek).where(m.TenantDek.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if row is not None:
        return kp.unwrap_dek(row.wrapped_dek)
    dek = AESGCM.generate_key(bit_length=256)
    db.add(
        m.TenantDek(tenant_id=tenant_id, wrapped_dek=kp.wrap_dek(dek), key_version=kp.key_version)
    )
    await db.flush()
    return dek


async def store_secret(
    db: AsyncSession, *, tenant_id: uuid.UUID, name: str, value: str, kind: str = "generic"
) -> m.Secret:
    """Encrypt and upsert a secret by (tenant_id, name)."""
    dek = await get_or_create_dek(db, tenant_id=tenant_id)
    kp = get_key_provider()
    nonce = os.urandom(12)
    ct = AESGCM(dek).encrypt(nonce, value.encode(), _aad(tenant_id, name))
    existing = (
        await db.execute(
            select(m.Secret).where(m.Secret.tenant_id == tenant_id, m.Secret.name == name)
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.ciphertext, existing.nonce = ct, nonce
        existing.key_version, existing.kind = kp.key_version, kind
        await db.flush()
        return existing
    sec = m.Secret(
        tenant_id=tenant_id,
        name=name,
        ciphertext=ct,
        nonce=nonce,
        key_version=kp.key_version,
        kind=kind,
    )
    db.add(sec)
    await db.flush()
    return sec


async def resolve_secret(db: AsyncSession, *, tenant_id: uuid.UUID, ref: str) -> str:
    """Decrypt and return the plaintext value for (tenant_id, ref)."""
    row = (
        await db.execute(
            select(m.Secret).where(m.Secret.tenant_id == tenant_id, m.Secret.name == ref)
        )
    ).scalar_one_or_none()
    if row is None:
        raise SecretNotFound(ref)
    dek = await get_or_create_dek(db, tenant_id=tenant_id)
    result: str = AESGCM(dek).decrypt(row.nonce, row.ciphertext, _aad(tenant_id, ref)).decode()
    return result


async def delete_secret(db: AsyncSession, *, tenant_id: uuid.UUID, ref: str) -> None:
    """Delete the secret at (tenant_id, ref), or raise `SecretNotFound`."""
    row = (
        await db.execute(
            select(m.Secret).where(m.Secret.tenant_id == tenant_id, m.Secret.name == ref)
        )
    ).scalar_one_or_none()
    if row is None:
        raise SecretNotFound(ref)
    await db.delete(row)
    await db.flush()
