from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag

from oc8.crypto.envelope import EnvelopeError
from oc8.crypto.framed_stream import FRAME_SIZE, decrypt_stream, encrypt_stream

_KEY = b"\x00" * 32
_AAD_PREFIX = b"evidence-archive-42"


def test_round_trips_a_single_small_chunk() -> None:
    frames = list(encrypt_stream([b"hello world"], key=_KEY, aad_prefix=_AAD_PREFIX))
    plaintext = b"".join(decrypt_stream(frames, key=_KEY, aad_prefix=_AAD_PREFIX))
    assert plaintext == b"hello world"


def test_round_trips_across_a_frame_boundary() -> None:
    original = b"x" * (FRAME_SIZE + 100)
    frames = list(encrypt_stream([original], key=_KEY, aad_prefix=_AAD_PREFIX))
    assert len(frames) == 2  # one full frame, one final partial frame
    plaintext = b"".join(decrypt_stream(frames, key=_KEY, aad_prefix=_AAD_PREFIX))
    assert plaintext == original


def test_round_trips_empty_input() -> None:
    frames = list(encrypt_stream([], key=_KEY, aad_prefix=_AAD_PREFIX))
    assert len(frames) == 1  # the empty final frame
    plaintext = b"".join(decrypt_stream(frames, key=_KEY, aad_prefix=_AAD_PREFIX))
    assert plaintext == b""


def test_caller_chunk_boundaries_need_not_align_with_frames() -> None:
    original = b"a" * 10 + b"b" * 10 + b"c" * 10
    frames = list(
        encrypt_stream([b"a" * 10, b"b" * 10, b"c" * 10], key=_KEY, aad_prefix=_AAD_PREFIX)
    )
    plaintext = b"".join(decrypt_stream(frames, key=_KEY, aad_prefix=_AAD_PREFIX))
    assert plaintext == original


def test_decrypt_rejects_a_reordered_frame() -> None:
    frames = list(encrypt_stream([b"a" * (FRAME_SIZE + 5)], key=_KEY, aad_prefix=_AAD_PREFIX))
    assert len(frames) == 2
    swapped = [frames[1], frames[0]]
    with pytest.raises(InvalidTag):
        list(decrypt_stream(swapped, key=_KEY, aad_prefix=_AAD_PREFIX))


def test_decrypt_rejects_a_truncated_frame() -> None:
    frames = list(encrypt_stream([b"hello"], key=_KEY, aad_prefix=_AAD_PREFIX))
    truncated = [frames[0][:-5]]
    with pytest.raises(EnvelopeError, match="length mismatch"):
        list(decrypt_stream(truncated, key=_KEY, aad_prefix=_AAD_PREFIX))


def test_decrypt_rejects_an_empty_stream() -> None:
    with pytest.raises(EnvelopeError, match="empty frame stream"):
        list(decrypt_stream([], key=_KEY, aad_prefix=_AAD_PREFIX))


def test_decrypt_rejects_a_frame_too_short_to_have_a_header() -> None:
    with pytest.raises(EnvelopeError, match="too short"):
        list(decrypt_stream([b"short"], key=_KEY, aad_prefix=_AAD_PREFIX))
