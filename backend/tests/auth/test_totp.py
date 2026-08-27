from __future__ import annotations

import time

from oc8.auth.totp import (
    generate_backup_codes,
    generate_secret,
    hash_backup_code,
    provisioning_uri,
    verify_backup_code,
    verify_code,
)


def test_generate_secret_is_base32_and_reasonably_long() -> None:
    secret = generate_secret()
    assert len(secret) >= 16
    assert secret.isupper() or secret.isalnum()  # base32 alphabet, no lowercase


def test_provisioning_uri_shape() -> None:
    uri = provisioning_uri(generate_secret(), account_name="admin@example.com", issuer="oc8")
    assert uri.startswith("otpauth://totp/")
    assert "admin%40example.com" in uri or "admin@example.com" in uri
    assert "issuer=oc8" in uri


def test_verify_code_accepts_the_current_valid_code() -> None:
    import pyotp

    secret = generate_secret()
    current_code = pyotp.TOTP(secret).now()
    assert verify_code(secret, current_code) is True


def test_verify_code_rejects_a_wrong_code() -> None:
    secret = generate_secret()
    assert verify_code(secret, "000000") is False


def test_verify_code_within_one_step_tolerance() -> None:
    import pyotp

    secret = generate_secret()
    totp = pyotp.TOTP(secret)
    # A code for 30s ago must still verify (the spec's +/-1 step window).
    past_code = totp.at(int(time.time()) - 30)
    assert verify_code(secret, past_code) is True


def test_generate_backup_codes_returns_ten_unique_codes() -> None:
    codes = generate_backup_codes()
    assert len(codes) == 10
    assert len(set(codes)) == 10


def test_backup_code_hash_round_trips() -> None:
    code = generate_backup_codes(count=1)[0]
    hashed = hash_backup_code(code)
    assert verify_backup_code(code, hashed) is True
    assert verify_backup_code("wrong-code", hashed) is False
