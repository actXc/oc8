from __future__ import annotations

import base64

import pytest

from oc8.config import Settings
from oc8.secrets.keyprovider import SecretStoreUnavailable, audit_mac_key

_KEK = base64.b64encode(b"\x11" * 32).decode()
_OTHER = base64.b64encode(b"\x22" * 32).decode()


def test_key_is_deterministic_and_32_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OC8_SECRET_KEK", _KEK)
    from oc8.config import get_settings

    get_settings.cache_clear()
    k1 = audit_mac_key()
    k2 = audit_mac_key()
    assert k1 == k2
    assert len(k1) == 32
    get_settings.cache_clear()


def test_key_differs_from_the_kek_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """Domain separation: the derived key must not be usable as the KEK."""
    monkeypatch.setenv("OC8_SECRET_KEK", _KEK)
    from oc8.config import get_settings

    get_settings.cache_clear()
    assert audit_mac_key() != base64.b64decode(_KEK)
    get_settings.cache_clear()


def test_different_keks_derive_different_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from oc8.config import get_settings

    monkeypatch.setenv("OC8_SECRET_KEK", _KEK)
    get_settings.cache_clear()
    a = audit_mac_key()
    monkeypatch.setenv("OC8_SECRET_KEK", _OTHER)
    get_settings.cache_clear()
    b = audit_mac_key()
    assert a != b
    get_settings.cache_clear()


def test_missing_kek_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OC8_SECRET_KEK", "")
    from oc8.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(SecretStoreUnavailable):
        audit_mac_key()
    get_settings.cache_clear()


def test_enabling_the_flag_without_a_kek_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="audit_mac_enabled"):
        Settings(audit_mac_enabled=True, secret_kek="")


def test_enabling_the_flag_with_a_valid_kek_constructs() -> None:
    s = Settings(audit_mac_enabled=True, secret_kek=_KEK)
    assert s.audit_mac_enabled is True
