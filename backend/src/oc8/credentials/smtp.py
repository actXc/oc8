"""Credential-type test for smtp_server (unified credentials framework).

Opens a real SMTP connection and, if credentials were given, authenticates
-- the cheapest real proof the server is reachable and the credentials
work, without sending an actual message. Mirrors `validate_s3`
(`capas/s3_source/credential.py`): an async callable taking the resolved
field values, raising on any failure. `test_credential`
(`oc8.credentials.service`) awaits it and converts whatever comes out into
a `ValueError` carrying the message, so an `smtplib` exception here
reaches the admin with the server's own wording (e.g. the 535 auth
rejection) rather than a flattened "test failed".
"""

from __future__ import annotations

import asyncio
import smtplib

# The de-facto implicit-TLS ("SMTPS") port. Unlike 587, a server listening
# here expects the TLS handshake FIRST -- there is no plaintext greeting to
# read and no STARTTLS command to issue, so `smtplib.SMTP` would simply
# block until the timeout. Ports 587/25 take the opposite path: connect in
# plaintext, then upgrade with STARTTLS.
_IMPLICIT_TLS_PORT = 465

_CONNECT_TIMEOUT_SECONDS = 10


def _check(host: str, port: int, username: str, password: str, use_tls: bool) -> None:
    """Blocking connect/handshake/auth. Run via `asyncio.to_thread`."""
    implicit_tls = use_tls and port == _IMPLICIT_TLS_PORT
    factory: type[smtplib.SMTP] = smtplib.SMTP_SSL if implicit_tls else smtplib.SMTP
    with factory(host, port, timeout=_CONNECT_TIMEOUT_SECONDS) as client:
        client.ehlo()
        if use_tls and not implicit_tls:
            # Raises SMTPNotSupportedError if the server never advertised
            # STARTTLS -- a genuine misconfiguration worth surfacing rather
            # than silently continuing in plaintext with a password.
            client.starttls()
            # A second EHLO is mandatory after STARTTLS: the pre-TLS
            # capability list (crucially AUTH) is discarded on upgrade, and
            # without it `login` may not see the server's auth mechanisms.
            client.ehlo()
        if username:
            client.login(username, password)


async def validate_smtp(values: dict[str, str]) -> None:
    host = values.get("host", "")
    if not host:
        raise ValueError("host is required")
    port_raw = values.get("port", "587")
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValueError(f"port must be a number, got {port_raw!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"port must be between 1 and 65535, got {port}")
    username = values.get("username", "")
    password = values.get("password", "")
    # Anything other than an explicit "false" means "use TLS" -- an empty or
    # missing value falls back to the field's own declared default ("true"),
    # so the secure path is what a half-filled form gets.
    use_tls = values.get("use_tls", "true").strip().lower() != "false"
    # smtplib is blocking; run it off the event loop so a slow/unreachable
    # SMTP host doesn't stall every other request this process is serving.
    await asyncio.to_thread(_check, host, port, username, password, use_tls)
