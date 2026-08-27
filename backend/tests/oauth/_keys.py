"""A real, parseable 2048-bit RSA private key for tests that sign a JWT --
an unparseable fake PEM string makes `jwt.encode` raise BEFORE any HTTP call
is made, so `httpx.MockTransport` never sees a request and the test fails at
the signing step with an error that looks nothing like what's being tested."""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Generated once at import time (module scope), not per-test -- a 2048-bit
# RSA keypair takes real time to generate and every affected test only needs
# a key that parses and signs, not a distinct one.
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

TEST_PRIVATE_KEY_PEM = _KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
