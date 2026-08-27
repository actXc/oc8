from __future__ import annotations

import uuid

import pytest

from oc8.crypto import envelope


def test_aad_for_binds_tenant_and_label() -> None:
    tenant_id = uuid.uuid4()
    aad = envelope.aad_for(tenant_id, "kb_chunk.content")
    assert aad == b"oc8:col:v1|" + f"{tenant_id}|kb_chunk.content".encode()


def test_aad_for_differs_by_tenant() -> None:
    label = "kb_chunk.content"
    assert envelope.aad_for(uuid.uuid4(), label) != envelope.aad_for(uuid.uuid4(), label)


def test_aad_for_differs_by_label() -> None:
    tenant_id = uuid.uuid4()
    assert envelope.aad_for(tenant_id, "a.b") != envelope.aad_for(tenant_id, "c.d")


def test_pack_unpack_round_trip() -> None:
    nonce = b"\x01" * envelope.NONCE_LEN
    packed = envelope.pack(
        purpose="content", generation=1, nonce=nonce, ciphertext=b"ciphertext-and-tag"
    )
    env = envelope.unpack(packed)
    assert env.purpose == "content"
    assert env.generation == 1
    assert env.nonce == nonce
    assert env.ciphertext == b"ciphertext-and-tag"


def test_pack_round_trips_every_purpose() -> None:
    for purpose in ("secrets", "content", "evidence"):
        packed = envelope.pack(
            purpose=purpose,
            generation=7,
            nonce=b"\x00" * envelope.NONCE_LEN,
            ciphertext=b"x",
        )
        assert envelope.unpack(packed).purpose == purpose


def test_pack_rejects_unknown_purpose() -> None:
    with pytest.raises(envelope.EnvelopeError, match="unknown purpose"):
        envelope.pack(
            purpose="bogus", generation=1, nonce=b"\x00" * envelope.NONCE_LEN, ciphertext=b"x"
        )


def test_pack_rejects_wrong_nonce_length() -> None:
    with pytest.raises(envelope.EnvelopeError, match="nonce must be"):
        envelope.pack(purpose="content", generation=1, nonce=b"\x00" * 5, ciphertext=b"x")


def test_pack_rejects_generation_out_of_range() -> None:
    with pytest.raises(envelope.EnvelopeError, match="out of range"):
        envelope.pack(
            purpose="content",
            generation=70000,
            nonce=b"\x00" * envelope.NONCE_LEN,
            ciphertext=b"x",
        )


def test_pack_accepts_generation_zero_and_max() -> None:
    for generation in (0, 0xFFFF):
        packed = envelope.pack(
            purpose="content",
            generation=generation,
            nonce=b"\x00" * envelope.NONCE_LEN,
            ciphertext=b"x",
        )
        assert envelope.unpack(packed).generation == generation


def test_unpack_rejects_too_short_input() -> None:
    with pytest.raises(envelope.EnvelopeError, match="too short"):
        envelope.unpack(b"\x00\x00")


def test_unpack_rejects_unsupported_version() -> None:
    packed = bytearray(
        envelope.pack(
            purpose="content", generation=1, nonce=b"\x00" * envelope.NONCE_LEN, ciphertext=b"x"
        )
    )
    packed[0] = 99
    with pytest.raises(envelope.EnvelopeError, match="unsupported envelope version"):
        envelope.unpack(bytes(packed))


def test_unpack_rejects_unknown_purpose_code() -> None:
    packed = bytearray(
        envelope.pack(
            purpose="content", generation=1, nonce=b"\x00" * envelope.NONCE_LEN, ciphertext=b"x"
        )
    )
    packed[1] = 99
    with pytest.raises(envelope.EnvelopeError, match="unknown purpose code"):
        envelope.unpack(bytes(packed))
