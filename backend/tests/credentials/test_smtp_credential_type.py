"""`smtp_server` credential type + its `validate_smtp` test entry point.

The socket work is mocked: these assert the DECISIONS `validate_smtp`
makes (reject before dialling, plaintext-then-STARTTLS vs. implicit TLS,
auth only when a username was given) and that real server-side failures
travel back out to `test_credential` rather than being swallowed.
"""

from __future__ import annotations

import smtplib
import ssl
from unittest.mock import MagicMock, patch

import pytest

from oc8.credentials import core_types  # noqa: F401 -- import triggers registration
from oc8.credentials.registry import CORE_CREDENTIAL_TYPES
from oc8.credentials.smtp import validate_smtp


def _smtp_client(smtp_cls: MagicMock) -> MagicMock:
    """Wire a patched SMTP class up so `with SMTP(...)` yields a mock."""
    client = MagicMock()
    smtp_cls.return_value.__enter__.return_value = client
    return client


# --- registration --------------------------------------------------------


def test_smtp_server_is_registered_as_a_core_credential_type() -> None:
    spec = CORE_CREDENTIAL_TYPES["smtp_server"]
    assert spec.display_name == "SMTP server"
    assert spec.validate_entry_point == "oc8.credentials.smtp:validate_smtp"
    assert [f.key for f in spec.fields] == [
        "host",
        "port",
        "username",
        "password",
        "from_address",
        "use_tls",
    ]


def test_only_the_password_field_is_secret() -> None:
    """`create_credential` routes exactly the kind="password" fields into
    the secret store; everything else stays readable in `field_values`."""
    spec = CORE_CREDENTIAL_TYPES["smtp_server"]
    assert [f.key for f in spec.fields if f.kind == "password"] == ["password"]


def test_username_and_password_are_optional() -> None:
    """Anonymous relays are a real deployment. `test_credential` only
    re-raises `CredentialFieldNotSet` for REQUIRED fields, so these two
    simply arrive absent from `values`."""
    by_key = {f.key: f for f in CORE_CREDENTIAL_TYPES["smtp_server"].fields}
    assert by_key["username"].required is False
    assert by_key["password"].required is False
    assert by_key["host"].required is True
    assert by_key["from_address"].required is True


# --- input validation, before any socket is opened -----------------------


async def test_missing_host_is_rejected() -> None:
    with pytest.raises(ValueError, match="host is required"):
        await validate_smtp({"host": ""})


async def test_non_numeric_port_is_rejected() -> None:
    with pytest.raises(ValueError, match="port must be a number"):
        await validate_smtp({"host": "smtp.example.com", "port": "not-a-number"})


@pytest.mark.parametrize("port", ["0", "-1", "70000"])
async def test_out_of_range_port_is_rejected(port: str) -> None:
    with pytest.raises(ValueError, match="port must be between 1 and 65535"):
        await validate_smtp({"host": "smtp.example.com", "port": port})


async def test_a_rejected_port_never_opens_a_connection() -> None:
    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        with pytest.raises(ValueError):
            await validate_smtp({"host": "smtp.example.com", "port": "0"})
    smtp_cls.assert_not_called()


# --- connection behaviour ------------------------------------------------


async def test_a_reachable_server_with_no_credentials_succeeds() -> None:
    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = _smtp_client(smtp_cls)
        await validate_smtp({"host": "smtp.example.com", "port": "587", "use_tls": "true"})
        client.starttls.assert_called_once()
        client.login.assert_not_called()
        # EHLO before the upgrade and again after it -- the post-STARTTLS
        # capability list is what `login` reads its mechanisms from.
        assert client.ehlo.call_count == 2


async def test_credentials_are_used_when_given() -> None:
    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = _smtp_client(smtp_cls)
        await validate_smtp(
            {
                "host": "smtp.example.com",
                "port": "587",
                "username": "user@example.com",
                "password": "secret",
            }
        )
        client.login.assert_called_once_with("user@example.com", "secret")


async def test_use_tls_false_skips_starttls() -> None:
    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = _smtp_client(smtp_cls)
        await validate_smtp({"host": "smtp.example.com", "port": "25", "use_tls": "False"})
        client.starttls.assert_not_called()


@pytest.mark.parametrize("use_tls", ["", "   "])
async def test_a_blank_use_tls_still_encrypts(use_tls: str) -> None:
    """A half-filled form must not silently downgrade to plaintext."""
    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = _smtp_client(smtp_cls)
        await validate_smtp({"host": "smtp.example.com", "port": "587", "use_tls": use_tls})
        client.starttls.assert_called_once()


async def test_port_465_uses_implicit_tls_instead_of_starttls() -> None:
    """A server on 465 expects the TLS handshake first: there is no
    plaintext greeting to read and no STARTTLS to issue, so a plain
    `smtplib.SMTP` there just hangs until the timeout."""
    with (
        patch("oc8.credentials.smtp.smtplib.SMTP_SSL") as ssl_cls,
        patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls,
    ):
        client = _smtp_client(ssl_cls)
        await validate_smtp(
            {
                "host": "smtp.example.com",
                "port": "465",
                "username": "u",
                "password": "p",
            }
        )
        smtp_cls.assert_not_called()
        ssl_cls.assert_called_once()
        client.starttls.assert_not_called()
        client.login.assert_called_once_with("u", "p")


# --- TLS policy ----------------------------------------------------------


async def test_starttls_verifies_the_servers_certificate() -> None:
    """smtplib's own default context is `check_hostname=False`/`CERT_NONE`:
    encrypted, but with no proof of who is on the other end. Passing an
    explicit default context is what turns that into real verification --
    and `oc8.mail.send` gets it from the same helper, so Test and Send can
    never disagree about whether a host is trusted."""
    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = _smtp_client(smtp_cls)
        await validate_smtp({"host": "smtp.example.com", "port": "587"})
    context = client.starttls.call_args.kwargs["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is True
    assert context.verify_mode is ssl.CERT_REQUIRED


async def test_implicit_tls_verifies_the_servers_certificate() -> None:
    with patch("oc8.credentials.smtp.smtplib.SMTP_SSL") as ssl_cls:
        _smtp_client(ssl_cls)
        await validate_smtp({"host": "smtp.example.com", "port": "465"})
    context = ssl_cls.call_args.kwargs["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is True
    assert context.verify_mode is ssl.CERT_REQUIRED


# --- failures reach the caller -------------------------------------------


async def test_an_auth_failure_propagates() -> None:
    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = _smtp_client(smtp_cls)
        client.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")
        with pytest.raises(smtplib.SMTPAuthenticationError):
            await validate_smtp(
                {"host": "smtp.example.com", "port": "587", "username": "u", "password": "wrong"}
            )


async def test_an_unreachable_host_propagates() -> None:
    """Distinct from an auth failure: nothing to authenticate against at
    all. `test_credential` turns either into a ValueError carrying this
    message, so the admin sees which of the two happened."""
    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        smtp_cls.side_effect = OSError("connection refused")
        with pytest.raises(OSError, match="connection refused"):
            await validate_smtp({"host": "smtp.example.com", "port": "587"})


async def test_a_server_without_starttls_support_propagates() -> None:
    with patch("oc8.credentials.smtp.smtplib.SMTP") as smtp_cls:
        client = _smtp_client(smtp_cls)
        client.starttls.side_effect = smtplib.SMTPNotSupportedError("STARTTLS not supported")
        with pytest.raises(smtplib.SMTPNotSupportedError):
            await validate_smtp({"host": "smtp.example.com", "port": "587"})
        client.login.assert_not_called()
