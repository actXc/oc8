"""Fixed-size framed AEAD stream, for content too large for one column value
(design spec §8.1) -- the evidence archive is its first consumer, a later
rollout step. Ships now because it is one of the four files spec §9 step 1
names explicitly, and it is fully testable on its own with synthetic bytes.

Each frame is independently authenticated and bound (via AAD) to its index
and to whether it is the final frame, so truncating or reordering frames is
detected on decrypt rather than silently accepted.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from oc8.crypto.envelope import NONCE_LEN, EnvelopeError

FRAME_SIZE = 64 * 1024


def _frame_aad(aad_prefix: bytes, index: int, *, final: bool) -> bytes:
    return aad_prefix + b"|" + str(index).encode() + b"|" + (b"1" if final else b"0")


def _encrypt_frame(
    aesgcm: AESGCM, frame_plaintext: bytes, index: int, aad_prefix: bytes, *, final: bool
) -> bytes:
    nonce = os.urandom(NONCE_LEN)
    aad = _frame_aad(aad_prefix, index, final=final)
    ciphertext = aesgcm.encrypt(nonce, frame_plaintext, aad)
    return nonce + len(ciphertext).to_bytes(4, "big") + ciphertext


def encrypt_stream(chunks: Iterable[bytes], *, key: bytes, aad_prefix: bytes) -> Iterator[bytes]:
    """Encrypt a stream of plaintext chunks into a stream of self-describing
    frames: `nonce(12) | length(4, BE) | AESGCM(frame_plaintext) || tag`.
    Buffers input into `FRAME_SIZE`-byte frames; the caller's chunk
    boundaries do not need to line up with frame boundaries. Always yields
    at least one frame (the final one, possibly empty), even for empty input.
    """
    aesgcm = AESGCM(key)
    buffer = bytearray()
    index = 0
    for chunk in chunks:
        buffer += chunk
        while len(buffer) >= FRAME_SIZE:
            frame_plaintext = bytes(buffer[:FRAME_SIZE])
            del buffer[:FRAME_SIZE]
            yield _encrypt_frame(aesgcm, frame_plaintext, index, aad_prefix, final=False)
            index += 1
    yield _encrypt_frame(aesgcm, bytes(buffer), index, aad_prefix, final=True)


def _decrypt_frame(
    aesgcm: AESGCM, frame: bytes, index: int, aad_prefix: bytes, *, final: bool
) -> bytes:
    if len(frame) < NONCE_LEN + 4:
        raise EnvelopeError(f"frame {index} too short: {len(frame)} bytes")
    nonce = frame[:NONCE_LEN]
    length = int.from_bytes(frame[NONCE_LEN : NONCE_LEN + 4], "big")
    ciphertext = frame[NONCE_LEN + 4 :]
    if len(ciphertext) != length:
        raise EnvelopeError(
            f"frame {index} length mismatch: header says {length}, got {len(ciphertext)}"
        )
    aad = _frame_aad(aad_prefix, index, final=final)
    return aesgcm.decrypt(nonce, ciphertext, aad)


def decrypt_stream(
    frames: Iterable[bytes], *, key: bytes, aad_prefix: bytes
) -> Iterator[bytes]:
    """Inverse of `encrypt_stream`. Raises `EnvelopeError` if a frame is
    truncated or the stream is empty, and raises `cryptography.exceptions
    .InvalidTag` if a frame is tampered with, reordered, or dropped from the
    middle (the AAD binds index and final-flag, so any of those changes the
    expected AAD and fails authentication).

    Holds exactly one frame back (rather than buffering the whole stream) so
    it can tell which frame is final without materialising `frames` in full
    -- the source may be arbitrarily large.
    """
    aesgcm = AESGCM(key)
    iterator = iter(frames)
    try:
        held = next(iterator)
    except StopIteration:
        raise EnvelopeError("empty frame stream: no final frame present") from None
    index = 0
    for frame in iterator:
        yield _decrypt_frame(aesgcm, held, index, aad_prefix, final=False)
        held = frame
        index += 1
    yield _decrypt_frame(aesgcm, held, index, aad_prefix, final=True)
