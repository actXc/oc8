"""Tests for the passphrase-bound secrets envelope (design doc §4).

The envelope is what makes an export's `secrets.json` portable off the
INSTANCE that produced it: secret values at rest are encrypted with the
tenant's DEK, itself wrapped by this instance's `OC8_SECRET_KEK`, so that
ciphertext decrypts nowhere else. `encrypt_secrets`/`decrypt_secrets` swap
that instance-bound key for one derived from an operator-supplied
passphrase instead.
"""

from __future__ import annotations

import base64
import uuid

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.backup.secrets_envelope import WrongPassphrase, decrypt_secrets, encrypt_secrets
from oc8.secrets.service import resolve_secret, store_secret
from tests.conftest import AppSessionFactory


@pytest.fixture(autouse=True)
def _kek(monkeypatch: pytest.MonkeyPatch) -> None:
    """`get_or_create_dek`/`store_secret`/`resolve_secret` all resolve the
    instance KEK via `get_settings()`; the cross-tenant round-trip tests
    below exercise those functions, so they need this set exactly like
    `tests/secrets/test_service.py` does."""
    from oc8 import config

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )


def test_round_trips_with_the_correct_passphrase() -> None:
    secrets = {"openai/api_key": "sk-abc123", "odoo/password": "hunter2"}
    envelope, meta = encrypt_secrets(secrets, "correct horse battery staple")
    assert meta["kdf"] == "scrypt" and meta["count"] == 2 and "salt" in meta
    assert meta["n"] == 16384 and meta["r"] == 8 and meta["p"] == 1
    decrypted = decrypt_secrets(envelope, "correct horse battery staple", meta)
    assert decrypted == secrets


def test_wrong_passphrase_raises_and_reveals_nothing() -> None:
    envelope, meta = encrypt_secrets({"a": "b"}, "right-pass")
    with pytest.raises(WrongPassphrase):
        decrypt_secrets(envelope, "wrong-pass", meta)


def test_empty_secret_set_still_round_trips() -> None:
    envelope, meta = encrypt_secrets({}, "pass")
    assert meta["count"] == 0
    assert decrypt_secrets(envelope, "pass", meta) == {}


def test_two_exports_use_independent_random_salts() -> None:
    """A fixed salt would make every export of the same secrets under the
    same passphrase produce identical ciphertext -- a fingerprinting leak
    the design doc's "fresh 32-byte random salt" avoids."""
    _, meta1 = encrypt_secrets({"a": "b"}, "pass")
    _, meta2 = encrypt_secrets({"a": "b"}, "pass")
    assert meta1["salt"] != meta2["salt"]
    assert len(base64.b64decode(meta1["salt"])) == 32


def test_tampered_ciphertext_is_rejected_not_silently_garbled() -> None:
    """AES-GCM's authentication tag, not merely a passphrase check, is what
    makes a corrupted or edited envelope a hard failure."""
    envelope, meta = encrypt_secrets({"a": "b"}, "pass")
    tampered = bytearray(envelope)
    tampered[-2] ^= 0xFF
    with pytest.raises((WrongPassphrase, ValueError)):
        decrypt_secrets(bytes(tampered), "pass", meta)


async def test_wrong_passphrase_on_import_stores_nothing(
    app_session: AppSessionFactory,
) -> None:
    """Decryption is verified before any write happens: a caller that
    decrypts fully before calling `store_secret` can never leave a target
    tenant with a partial set of restored secrets."""
    tenant_id = uuid.uuid4()
    envelope, meta = encrypt_secrets({"gh": "ghp_secret", "odoo": "hunter2"}, "right-pass")

    async with app_session(tenant_id) as db:
        with pytest.raises(WrongPassphrase):
            decrypted = decrypt_secrets(envelope, "wrong-pass", meta)
            # Never reached with the wrong passphrase -- proves the
            # decrypt-then-store pattern cannot partially apply.
            for name, value in decrypted.items():
                await store_secret(db, tenant_id=tenant_id, name=name, value=value)

    async with app_session(tenant_id) as db:
        rows = (
            (await db.execute(select(m.Secret).where(m.Secret.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
        assert rows == []


async def test_cross_tenant_import_decrypts_under_the_target_tenants_dek(
    app_session: AppSessionFactory,
) -> None:
    """A secret exported from tenant A and imported into tenant B is
    re-encrypted under B's own DEK via `store_secret`, and reads back
    through B's normal `resolve_secret` -- the envelope carries plaintext
    only in transit, never DEK-wrapped ciphertext across tenants."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    originals = {"openai/api_key": "sk-abc123", "odoo/password": "hunter2"}

    async with app_session(tenant_a) as db:
        for name, value in originals.items():
            await store_secret(db, tenant_id=tenant_a, name=name, value=value)

    envelope, meta = encrypt_secrets(originals, "shared-passphrase")
    decrypted = decrypt_secrets(envelope, "shared-passphrase", meta)

    async with app_session(tenant_b) as db:
        for name, value in decrypted.items():
            await store_secret(db, tenant_id=tenant_b, name=name, value=value)

    async with app_session(tenant_b) as db:
        for name, value in originals.items():
            assert await resolve_secret(db, tenant_id=tenant_b, ref=name) == value

    # And tenant A's own secrets are unaffected and still resolve under A's DEK.
    async with app_session(tenant_a) as db:
        for name, value in originals.items():
            assert await resolve_secret(db, tenant_id=tenant_a, ref=name) == value


async def test_import_without_secrets_json_leaves_existing_secrets_untouched(
    app_session: AppSessionFactory,
) -> None:
    """`manifest.secrets is None` means "the operator supplied no
    passphrase, or the archive has none" -- a caller must simply skip the
    decrypt/store step, never delete what is already there. This test
    stands in for that caller: nothing in this module is invoked when there
    is no envelope, so a tenant's existing secrets are simply never touched.
    """
    tenant_id = uuid.uuid4()
    async with app_session(tenant_id) as db:
        await store_secret(db, tenant_id=tenant_id, name="gh", value="ghp_secret")

    # An import that found `manifest.secrets is None` does nothing here --
    # no decrypt_secrets call, no store_secret call.

    async with app_session(tenant_id) as db:
        assert await resolve_secret(db, tenant_id=tenant_id, ref="gh") == "ghp_secret"


def test_bad_scrypt_params_in_meta_are_used_verbatim_and_fail_closed() -> None:
    """`decrypt_secrets` trusts `meta["n"/"r"/"p"]` from the archive rather
    than hardcoding its own constants, so a manifest edited to use different
    KDF parameters than the ones actually used to encrypt simply fails the
    GCM tag check rather than silently deriving the right key some other
    way."""
    envelope, meta = encrypt_secrets({"a": "b"}, "pass")
    tampered_meta = dict(meta, n=1024)
    with pytest.raises(WrongPassphrase):
        decrypt_secrets(envelope, "pass", tampered_meta)


def test_manifest_kdf_params_are_authenticated_not_just_used() -> None:
    """Editing the manifest's KDF metadata must fail the tag, not the key derivation.

    `salt`, `n`, `r` and `p` sit in manifest.json, outside the envelope, so
    they are attacker-editable. Without AAD the only thing stopping tampering
    is the incidental fact that a different salt derives a different key. This
    pins the stronger property: `count`, which does NOT feed the KDF at all,
    is also covered by the tag -- so the manifest metadata as a whole is
    authenticated rather than merely consulted.
    """
    from oc8.backup.secrets_envelope import WrongPassphrase, decrypt_secrets, encrypt_secrets

    blob, meta = encrypt_secrets({"model/anthropic/api_key": "sk-real"}, "correct horse")

    assert decrypt_secrets(blob, "correct horse", meta) == {"model/anthropic/api_key": "sk-real"}

    tampered = dict(meta) | {"count": meta["count"] + 7}
    with pytest.raises(WrongPassphrase):
        decrypt_secrets(blob, "correct horse", tampered)
