from __future__ import annotations

import os
import uuid

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy.engine.default import DefaultDialect

from oc8.crypto import envelope
from oc8.crypto.context import CryptoContextMissing, TenantCrypto, bound_tenant_crypto
from oc8.crypto.envelope import EnvelopeError
from oc8.crypto.types import DeterministicText, EncryptedJSON, EncryptedText

# `TypeDecorator.process_bind_param`/`process_result_value` are typed to take a
# real `Dialect`; a concrete one that never touches a DB connection keeps
# these calls mypy-clean without a `# type: ignore`.
_DIALECT = DefaultDialect()


def _crypto(tenant_id: uuid.UUID | None = None, generation: int = 1) -> TenantCrypto:
    return TenantCrypto(
        tenant_id=tenant_id or uuid.uuid4(),
        keys={"content": (generation, b"\x00" * 32)},
    )


def test_encrypted_text_round_trips() -> None:
    col = EncryptedText("test_table.text_column")
    with bound_tenant_crypto(_crypto()):
        stored = col.process_bind_param("hello world", _DIALECT)
        assert stored is not None
        assert col.process_result_value(stored, _DIALECT) == "hello world"


def test_encrypted_text_passes_through_none() -> None:
    col = EncryptedText("test_table.text_column")
    with bound_tenant_crypto(_crypto()):
        assert col.process_bind_param(None, _DIALECT) is None
        assert col.process_result_value(None, _DIALECT) is None


def test_encrypted_text_two_writes_of_same_value_differ() -> None:
    col = EncryptedText("test_table.text_column")
    with bound_tenant_crypto(_crypto()):
        a = col.process_bind_param("same value", _DIALECT)
        b = col.process_bind_param("same value", _DIALECT)
        assert a != b  # random nonce per write


def test_encrypted_text_rejects_wrong_tenant() -> None:
    col = EncryptedText("test_table.text_column")
    key = b"\x00" * 32
    with bound_tenant_crypto(TenantCrypto(tenant_id=uuid.uuid4(), keys={"content": (1, key)})):
        stored = col.process_bind_param("secret", _DIALECT)
    with bound_tenant_crypto(TenantCrypto(tenant_id=uuid.uuid4(), keys={"content": (1, key)})):
        with pytest.raises(InvalidTag):
            col.process_result_value(stored, _DIALECT)


def test_encrypted_text_rejects_generation_mismatch() -> None:
    col = EncryptedText("test_table.text_column")
    tenant_id = uuid.uuid4()
    with bound_tenant_crypto(
        TenantCrypto(tenant_id=tenant_id, keys={"content": (1, b"\x00" * 32)})
    ):
        stored = col.process_bind_param("secret", _DIALECT)
    with bound_tenant_crypto(
        TenantCrypto(tenant_id=tenant_id, keys={"content": (2, b"\x00" * 32)})
    ):
        with pytest.raises(EnvelopeError, match="does not match the bound key generation"):
            col.process_result_value(stored, _DIALECT)


def test_encrypted_json_round_trips_dict_and_list() -> None:
    col = EncryptedJSON("test_table.json_column")
    with bound_tenant_crypto(_crypto()):
        stored_dict = col.process_bind_param({"b": 2, "a": 1}, _DIALECT)
        assert col.process_result_value(stored_dict, _DIALECT) == {"b": 2, "a": 1}
        stored_list = col.process_bind_param([1, 2, 3], _DIALECT)
        assert col.process_result_value(stored_list, _DIALECT) == [1, 2, 3]


def test_encrypted_json_passes_through_none() -> None:
    col = EncryptedJSON("test_table.json_column")
    with bound_tenant_crypto(_crypto()):
        assert col.process_bind_param(None, _DIALECT) is None
        assert col.process_result_value(None, _DIALECT) is None


def test_deterministic_text_same_plaintext_yields_same_ciphertext() -> None:
    col = DeterministicText("test_table.label_column")
    with bound_tenant_crypto(_crypto()):
        a = col.process_bind_param("acme-prod", _DIALECT)
        b = col.process_bind_param("acme-prod", _DIALECT)
        assert a == b


def test_deterministic_text_different_plaintext_yields_different_ciphertext() -> None:
    col = DeterministicText("test_table.label_column")
    with bound_tenant_crypto(_crypto()):
        a = col.process_bind_param("acme-prod", _DIALECT)
        b = col.process_bind_param("acme-staging", _DIALECT)
        assert a != b


def test_deterministic_text_round_trips() -> None:
    col = DeterministicText("test_table.label_column")
    with bound_tenant_crypto(_crypto()):
        stored = col.process_bind_param("acme-prod", _DIALECT)
        assert col.process_result_value(stored, _DIALECT) == "acme-prod"


def test_deterministic_text_passes_through_none() -> None:
    col = DeterministicText("test_table.label_column")
    with bound_tenant_crypto(_crypto()):
        assert col.process_bind_param(None, _DIALECT) is None
        assert col.process_result_value(None, _DIALECT) is None


def _envelope_with_wrong_purpose() -> bytes:
    # `purpose` is not covered by the AAD -- it must never drive key
    # selection. Any ciphertext bytes will do since a wrong purpose is
    # rejected before decryption is attempted.
    return envelope.pack(
        purpose="secrets",
        generation=1,
        nonce=os.urandom(envelope.NONCE_LEN),
        ciphertext=b"irrelevant-ciphertext",
    )


def test_encrypted_text_rejects_wrong_purpose_byte() -> None:
    col = EncryptedText("test_table.text_column")
    with bound_tenant_crypto(_crypto()):
        with pytest.raises(EnvelopeError, match="purpose"):
            col.process_result_value(_envelope_with_wrong_purpose(), _DIALECT)


def test_encrypted_json_rejects_wrong_purpose_byte() -> None:
    col = EncryptedJSON("test_table.json_column")
    with bound_tenant_crypto(_crypto()):
        with pytest.raises(EnvelopeError, match="purpose"):
            col.process_result_value(_envelope_with_wrong_purpose(), _DIALECT)


def test_deterministic_text_rejects_wrong_purpose_byte() -> None:
    col = DeterministicText("test_table.label_column")
    with bound_tenant_crypto(_crypto()):
        with pytest.raises(EnvelopeError, match="purpose"):
            col.process_result_value(_envelope_with_wrong_purpose(), _DIALECT)


def test_encrypted_text_rejects_ciphertext_written_under_a_different_label() -> None:
    tenant_id = uuid.uuid4()
    crypto = _crypto(tenant_id)
    col_a = EncryptedText("test_table.a_column")
    col_b = EncryptedText("test_table.b_column")
    with bound_tenant_crypto(crypto):
        stored = col_a.process_bind_param("secret", _DIALECT)
        with pytest.raises(InvalidTag):
            col_b.process_result_value(stored, _DIALECT)


def test_encrypted_json_rejects_ciphertext_written_under_a_different_label() -> None:
    tenant_id = uuid.uuid4()
    crypto = _crypto(tenant_id)
    col_a = EncryptedJSON("test_table.a_column")
    col_b = EncryptedJSON("test_table.b_column")
    with bound_tenant_crypto(crypto):
        stored = col_a.process_bind_param({"secret": 1}, _DIALECT)
        with pytest.raises(InvalidTag):
            col_b.process_result_value(stored, _DIALECT)


def test_deterministic_text_rejects_ciphertext_written_under_a_different_label() -> None:
    tenant_id = uuid.uuid4()
    crypto = _crypto(tenant_id)
    col_a = DeterministicText("test_table.a_column")
    col_b = DeterministicText("test_table.b_column")
    with bound_tenant_crypto(crypto):
        stored = col_a.process_bind_param("acme-prod", _DIALECT)
        with pytest.raises(InvalidTag):
            col_b.process_result_value(stored, _DIALECT)


def test_encrypted_text_raises_when_no_crypto_context_bound() -> None:
    col = EncryptedText("test_table.text_column")
    with pytest.raises(CryptoContextMissing):
        col.process_bind_param("hello", _DIALECT)
    with pytest.raises(CryptoContextMissing):
        col.process_result_value(b"irrelevant", _DIALECT)


def test_encrypted_json_raises_when_no_crypto_context_bound() -> None:
    col = EncryptedJSON("test_table.json_column")
    with pytest.raises(CryptoContextMissing):
        col.process_bind_param({"a": 1}, _DIALECT)
    with pytest.raises(CryptoContextMissing):
        col.process_result_value(b"irrelevant", _DIALECT)


def test_deterministic_text_raises_when_no_crypto_context_bound() -> None:
    col = DeterministicText("test_table.label_column")
    with pytest.raises(CryptoContextMissing):
        col.process_bind_param("acme-prod", _DIALECT)
    with pytest.raises(CryptoContextMissing):
        col.process_result_value(b"irrelevant", _DIALECT)
